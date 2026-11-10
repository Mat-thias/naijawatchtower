"""FastAPI application (CLAUDE.md 5.3, 7). JSON API; HTML pages are added in `web_routes` (M3).

`create_app` takes the database URL, upload directory and a reader factory, so tests run
with a `FakeReader` and no GPU, the demo with `CacheReader`, and live mode with `VLMReader`.
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from naijawatchtower.api import audit, services
from naijawatchtower.api.db import (AuditLog, PollingUnit, ResultSheet, SheetJob, User, Verification,
                                    make_sessionmaker)
from naijawatchtower.api.pipeline import current_values
from naijawatchtower.api.security import (OtpStore, TokenBucket, make_token, phone_hash, read_token,
                                          ussd_secret)
from naijawatchtower.api.ussd import handle_ussd
from naijawatchtower.config import ROOT, field_names, rules_config
from naijawatchtower.ocr.types import SheetReader
from naijawatchtower.validate.rules import check_rules

UNOFFICIAL = ("UNOFFICIAL. Digitised from result-sheet images. Not an official declaration. "
              "Official results are declared by INEC only.")
DEMO_BANNER = "DEMO DATA: REDACTED REAL SHEETS AND SIMULATED RECORDS"


@dataclass
class Settings:
    db_url: str = os.environ.get("NWT_DB", f"sqlite:///{ROOT / 'data' / 'app.db'}")
    upload_dir: Path = ROOT / "data" / "uploads"
    reader_kind: str = "vlm"
    observer_invite_code: str = os.environ.get("OBSERVER_INVITE_CODE", "DEMO-OBSERVER")


# ------------------------------------------------------------------ request bodies

class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    otp: str
    pu_code: str | None = None
    invite_code: str | None = None
    opt_in_sms: bool = False


class CorrectionIn(BaseModel):
    field: str
    value: int | None = Field(default=None, ge=0, le=9999)


class VerificationIn(BaseModel):
    verdict: str
    comment: str | None = Field(default=None, max_length=500)
    field_corrections: list[CorrectionIn] = []


class ReviewIn(BaseModel):
    final_values: dict[str, int | None] = {}
    decision: str
    reason_code: str
    note: str | None = Field(default=None, max_length=1000)


def create_app(settings: Settings | None = None, reader_factory: Callable[[], SheetReader] | None = None,
               sessions: sessionmaker[Session] | None = None) -> FastAPI:
    """Build the app. The GPU worker starts with the app and loads the reader on its first job."""
    from naijawatchtower.api.worker import JobQueue

    settings = settings or Settings()
    sessions = sessions or make_sessionmaker(settings.db_url)
    if reader_factory is None:
        from naijawatchtower.ocr.factory import make_reader
        backend = os.environ.get("VLM_BACKEND", "cache")
        reader_factory = lambda: make_reader(backend)  # noqa: E731
    app = FastAPI(title="NaijaWatchTower (unofficial PoC)", docs_url="/api/docs", redoc_url=None)
    app.state.settings = settings
    app.state.sessions = sessions
    app.state.queue = JobQueue(sessions, reader_factory, settings.reader_kind)
    app.state.otp = OtpStore()
    app.state.ip_bucket = TokenBucket(services.vconfig()["rate_limits"]["requests_per_ip_per_minute"], 60.0)
    app.state.verify_bucket = TokenBucket(services.vconfig()["rate_limits"]["verifications_per_user_per_hour"],
                                          3600.0)

    def get_db():
        with sessions() as db:
            yield db

    def current_user(request: Request, db: Session = Depends(get_db),
                     authorization: str | None = Header(default=None)) -> User | None:
        token = (authorization or "").removeprefix("Bearer ").strip() or request.cookies.get("nwt_session", "")
        uid = read_token(token) if token else None
        return db.get(User, uid) if uid else None

    def require(*roles: str):
        def dep(user: User | None = Depends(current_user)) -> User:
            if user is None:
                raise HTTPException(401, "Sign in first.")
            if roles and user.role not in roles:
                raise HTTPException(403, f"This needs one of the roles: {', '.join(roles)}.")
            return user
        return dep

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        if request.url.path not in ("/healthz",) and not app.state.ip_bucket.allow(ip):
            return JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
        return await call_next(request)

    @app.exception_handler(services.ServiceError)
    async def service_error(_: Request, err: services.ServiceError):
        return JSONResponse({"detail": err.message, "code": err.code}, status_code=err.status)

    app.state.require = require
    app.state.current_user = current_user
    app.state.get_db = get_db

    # -------------------------------------------------------------- auth (simulated OTP)

    @app.post("/auth/otp/request")
    def otp_request(body: OtpRequest):
        try:
            app.state.otp.issue(phone_hash(body.phone))
        except ValueError:
            raise HTTPException(422, "Enter a valid phone number.") from None
        return {"sent": True, "demo_mode": "Simulated OTP: the code is printed in the server log. No SMS is sent."}

    @app.post("/auth/otp/verify")
    def otp_verify(body: OtpVerify, db: Session = Depends(get_db)):
        try:
            ph = phone_hash(body.phone)
        except ValueError:
            raise HTTPException(422, "Enter a valid phone number.") from None
        if not app.state.otp.check(ph, body.otp):
            raise HTTPException(401, "Wrong or expired code.")
        user = db.execute(select(User).where(User.phone_hash == ph)).scalar_one_or_none()
        if user is None:
            user = User(phone_hash=ph, role="citizen", opted_in_notifications=body.opt_in_sms)
            db.add(user)
        if body.invite_code and body.invite_code == settings.observer_invite_code and user.role == "citizen":
            user.role = "observer"
        if body.pu_code:
            unit = services.unit_by_code(db, body.pu_code)
            if unit is None:
                raise HTTPException(404, "Unknown polling-unit code.")
            user.pu_id = unit.id
        db.flush()
        audit.append(db, user.role, str(user.id), "signed_in", {"pu": user.pu_id, "channel": "web"})
        db.commit()
        token = make_token(user.id)
        resp = JSONResponse({"token": token, "role": user.role, "pu_id": user.pu_id})
        resp.set_cookie("nwt_session", token, httponly=True, samesite="lax")
        return resp

    # -------------------------------------------------------------- sheets

    @app.post("/sheets", status_code=202)
    async def upload_sheet(pu_code: str = Form(...), file: UploadFile = File(...),
                           user: User = Depends(require("ingest", "admin")), db: Session = Depends(get_db)):
        data = await file.read(services.MAX_UPLOAD_BYTES + 1)
        sheet = services.ingest_sheet(db, user, pu_code, data, settings.upload_dir)
        job = app.state.queue.submit(db, sheet.id)
        db.commit()
        app.state.queue.enqueue(job.id)
        return {"id": sheet.id, "status": sheet.status, "image_sha256": sheet.image_sha256, "job_id": job.id}

    @app.get("/sheets/{sheet_id}")
    def get_sheet(sheet_id: int, db: Session = Depends(get_db)):
        sheet = db.get(ResultSheet, sheet_id)
        if sheet is None:
            raise HTTPException(404, "No such sheet.")
        return sheet_detail(db, sheet)

    @app.get("/sheets/{sheet_id}/image")
    def sheet_image(sheet_id: int, db: Session = Depends(get_db)):
        sheet = db.get(ResultSheet, sheet_id)
        if sheet is None or not sheet.image_path or not Path(sheet.image_path).exists():
            raise HTTPException(404, "No image for this sheet.")
        return FileResponse(sheet.image_path, headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/pu/{code}")
    def get_unit(code: str, request: Request, db: Session = Depends(get_db)):
        if "text/html" in request.headers.get("accept", ""):
            return app.state.web_unit_page(request, code, db)
        unit = services.unit_by_code(db, code)
        if unit is None:
            raise HTTPException(404, "Unknown polling-unit code.")
        sheets = db.execute(select(ResultSheet).where(ResultSheet.pu_id == unit.id)).scalars().all()
        return {"unit": unit_json(unit), "sheets": [{"id": s.id, "status": s.status, "source": s.source}
                                                     for s in sheets], "banner": UNOFFICIAL, "demo": DEMO_BANNER}

    @app.post("/sheets/{sheet_id}/verifications", status_code=201)
    def verify(sheet_id: int, body: VerificationIn, user: User = Depends(require("citizen", "observer")),
               db: Session = Depends(get_db)):
        if not app.state.verify_bucket.allow(str(user.id)):
            raise HTTPException(429, "Too many verification attempts. Try again later.")
        sheet = db.get(ResultSheet, sheet_id)
        if sheet is None:
            raise HTTPException(404, "No such sheet.")
        result = services.record_verification(db, user, sheet, body.verdict, body.comment,
                                              [(c.field, c.value) for c in body.field_corrections])
        db.commit()
        return {"ref": result.case_ref, "status": result.status}

    @app.get("/review/queue")
    def queue(user: User = Depends(require("reviewer", "admin")), db: Session = Depends(get_db)):
        return {"queue": services.review_queue(db)}

    @app.post("/sheets/{sheet_id}/review")
    def review(sheet_id: int, body: ReviewIn, user: User = Depends(require("reviewer", "admin")),
               db: Session = Depends(get_db)):
        sheet = db.get(ResultSheet, sheet_id)
        if sheet is None:
            raise HTTPException(404, "No such sheet.")
        status = services.review_sheet(db, user, sheet, body.final_values, body.decision, body.reason_code,
                                       body.note)
        db.commit()
        return {"status": status}

    # -------------------------------------------------------------- aggregates and map

    @app.get("/aggregate")
    def aggregate(scope: str = "national", code: str | None = None, db: Session = Depends(get_db)):
        return {**services.aggregate(db, scope, code), "banner": UNOFFICIAL, "demo": DEMO_BANNER}

    @app.get("/geo/states")
    def geo_states(layer: str = "coverage", db: Session = Depends(get_db)):
        return {"layer": layer, "states": services.state_records(db, layer), "demo": DEMO_BANNER}

    @app.get("/geo/state/{code}")
    def geo_state(code: str, db: Session = Depends(get_db)):
        if not code.isdigit() or not 1 <= int(code) <= 37:
            raise HTTPException(404, "Unknown state.")
        return {**services.state_panel(db, f"{int(code):03d}"), "banner": UNOFFICIAL, "demo": DEMO_BANNER}

    # -------------------------------------------------------------- discrepancies, cases, audit

    @app.get("/discrepancies")
    def discrepancies(request: Request, status: str | None = None, type: str | None = None,
                      db: Session = Depends(get_db)):
        if "text/html" in request.headers.get("accept", ""):
            return app.state.web_discrepancies_page(request, status, type, db)
        return {"discrepancies": services.discrepancy_rows(db, status or None, type or None), "demo": DEMO_BANNER}

    @app.get("/export/discrepancies.json")
    def export_json(request: Request, status: str | None = None, type: str | None = None,
                    db: Session = Depends(get_db)):
        rows = services.discrepancy_rows(db, status, type)
        audit.append(db, "public", "export", "export_discrepancies", {"format": "json", "rows": len(rows)})
        db.commit()
        return {"notice": f"{DEMO_BANNER}. {UNOFFICIAL} An exportable report, subject to INEC's written "
                          "authorisation.", "rows": rows}

    @app.get("/export/discrepancies.csv")
    def export_csv(status: str | None = None, type: str | None = None, db: Session = Depends(get_db)):
        rows = services.discrepancy_rows(db, status, type)
        audit.append(db, "public", "export", "export_discrepancies", {"format": "csv", "rows": len(rows)})
        db.commit()
        out = io.StringIO()
        out.write(f"# {DEMO_BANNER}. {UNOFFICIAL}\n")
        fields = ["discrepancy_id", "sheet_id", "unit", "source", "image_sha256", "type", "status", "evidence",
                  "created_at", "resolved_at"]
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return PlainTextResponse(out.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=discrepancies.csv"})

    @app.get("/case/{ref}")
    def case(ref: str, db: Session = Depends(get_db)):
        found = services.case_status(db, ref)
        if found is None:
            raise HTTPException(404, "Not found.")
        return found

    @app.get("/audit/verify")
    def audit_verify(db: Session = Depends(get_db)):
        return audit.verify_chain(db)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # -------------------------------------------------------------- USSD

    @app.post("/ussd", response_class=PlainTextResponse)
    def ussd(sessionId: str = Form(""), serviceCode: str = Form(""), phoneNumber: str = Form(...),
             text: str = Form(""), x_ussd_secret: str | None = Header(default=None),
             db: Session = Depends(get_db)):
        if not x_ussd_secret or x_ussd_secret != ussd_secret():
            raise HTTPException(401, "Unauthorised")
        try:
            kind, message = handle_ussd(text, phoneNumber, "en", db)
        except ValueError:
            kind, message = "END", "[DEMO] Invalid phone number."
        return PlainTextResponse(f"{kind} {message}")

    # -------------------------------------------------------------- USSD simulator (demo tool)

    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "web" / "templates"))
    app.state.templates = templates

    @app.get("/ussd-sim")
    def ussd_sim(request: Request):
        return app.state.render(request, "ussd_sim.html", None, default_phone="08051234567")

    @app.post("/ussd-sim/send", response_class=PlainTextResponse)
    def ussd_sim_send(sessionId: str = Form(""), serviceCode: str = Form(""), phoneNumber: str = Form(...),
                      text: str = Form(""), db: Session = Depends(get_db)):
        """Server-side stand-in for a telecom aggregator: the secret never reaches the browser."""
        return ussd(sessionId, serviceCode, phoneNumber, text, ussd_secret(), db)

    # -------------------------------------------------------------- web pages, static files, gzip

    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.staticfiles import StaticFiles

    from naijawatchtower.web import routes as web_routes
    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "web" / "static")),
              name="static")
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    web_routes.register(app)
    return app


# ------------------------------------------------------------------ JSON helpers

def unit_json(unit: PollingUnit) -> dict[str, Any]:
    return {"code": unit.code, "numeric_code": unit.numeric_code, "name": unit.name, "state_code": unit.state_code,
            "state": unit.state_name, "zone": unit.zone, "lga": unit.lga_name, "lga_code": unit.lga_code,
            "ward": unit.ward_name, "ra_code": unit.ra_code, "source": unit.source}


def journey(db: Session, sheet: ResultSheet) -> list[dict[str, str]]:
    """Plain-language timeline from the audit log (CLAUDE.md 8.1)."""
    labels = {"sheet_uploaded": "Photo uploaded", "job_result": "Read by the system and arithmetic checked",
              "job_failed": "The system could not read the photo", "review_decision": "Human review",
              "discrepancy_opened": "Discrepancy opened for review"}
    steps: list[dict[str, str]] = []
    first_verification = None
    for row in db.execute(select(AuditLog).where(AuditLog.sheet_id == sheet.id).order_by(AuditLog.id)).scalars():
        if row.action in labels:
            steps.append({"step": labels[row.action], "ts": row.ts})
        elif row.action.startswith("verification_") and first_verification is None:
            first_verification = row.ts
            steps.append({"step": "Checked by citizens or observers", "ts": row.ts})
        elif row.action == "status_changed" and sheet.status == "VERIFIED":
            pass
    if sheet.status == "VERIFIED":
        steps.append({"step": "Verified by citizens or observers", "ts": ""})
    return steps


def sheet_detail(db: Session, sheet: ResultSheet) -> dict[str, Any]:
    """Everything the sheet page needs; verification counts only, never identities."""
    unit = db.get(PollingUnit, sheet.pu_id)
    cur = current_values(db, sheet.id)
    from naijawatchtower.validate.routing import thresholds
    conf_min = thresholds()["field_conf_min"]
    messages = {r["id"]: r["message"] for r in rules_config()["rules"]}
    fields = []
    for n in field_names():
        f = cur.get(n, {})
        label = (services.party_label(unit, n) if n.startswith("party_")  # type: ignore[arg-type]
                 else n.replace("_", " ").capitalize())
        fields.append({"name": n, "label": label, "value": f.get("value"), "model_value": f.get("model_value"),
                       "source": f.get("source"), "confidence": f.get("confidence"), "unclear": f.get("unclear", False),
                       "low_confidence": f.get("source") == "model" and (f.get("confidence") or 0) < conf_min,
                       "suggestion": f.get("suggestion") if f.get("has_suggestion") else None,
                       "has_suggestion": f.get("has_suggestion", False)})
    # Rules are checked live against the current values (model reading, or the reviewer's values);
    # validation_result keeps the pipeline's original check for the record.
    rules = ([{"rule_id": r.rule_id, "severity": r.severity, "passed": r.passed, "message": messages.get(r.rule_id)}
              for r in check_rules({n: cur[n]["value"] for n in cur})] if cur else [])
    counts = db.execute(select(Verification.verdict, func.count(), func.sum(Verification.weight))
                        .where(Verification.sheet_id == sheet.id).group_by(Verification.verdict)).all()
    job = db.execute(select(SheetJob).where(SheetJob.sheet_id == sheet.id).order_by(SheetJob.id.desc())
                     ).scalars().first()
    return {"id": sheet.id, "unit": unit_json(unit), "source": sheet.source, "status": sheet.status,  # type: ignore[arg-type]
            "image_url": f"/sheets/{sheet.id}/image" if sheet.image_path else None,
            "image_sha256": sheet.image_sha256, "pipeline_outcome": sheet.pipeline_outcome,
            "detail": json.loads(sheet.pipeline_detail_json or "{}"), "fields": fields, "rules": rules,
            "verification_counts": {v: {"count": c, "weight": w} for v, c, w in counts},
            "job": {"state": job.state, "seconds": job.seconds} if job else None,
            "model_revision": sheet.model_revision, "prompt_version": sheet.prompt_version,
            "journey": journey(db, sheet), "banner": UNOFFICIAL, "demo": DEMO_BANNER}
