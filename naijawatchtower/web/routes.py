"""HTML pages (CLAUDE.md 9). Server-rendered Jinja2 with vendored HTMX; every page works without JavaScript.

Every page extends base.html, which carries the UNOFFICIAL banner and the demo-data banner.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from naijawatchtower.api import audit, services
from naijawatchtower.api.app import sheet_detail
from naijawatchtower.api.db import AuditLog, PollingUnit, ResultSheet, User
from naijawatchtower.api.i18n import LANGUAGES, t
from naijawatchtower.api.pipeline import DIGITISED
from naijawatchtower.api.security import make_token, phone_hash
from naijawatchtower.api.seed import DEMO_PHONES
from naijawatchtower.web.mapsvg import describe, legend, render_map

LAYERS = ("coverage", "verified", "discrepancies", "accreditation")
ZONES = ("North Central", "North East", "North West", "South East", "South South", "South West")
DISCREPANCY_TYPES = ("ARITHMETIC_INCONSISTENT", "VERIFIER_DISPUTE", "MODEL_VS_HUMAN_MISMATCH", "UNREADABLE")
NAV = [("/", "web.nav.dashboard", None), ("/discrepancies", "web.nav.discrepancies", None),
       ("/review", "web.nav.review", ("reviewer", "admin")), ("/upload", "web.nav.upload", ("ingest", "admin")),
       ("/audit", "web.nav.audit", None),
       ("/how-it-works", "web.nav.how", None), ("/track", "web.nav.track", None),
       ("/ussd-sim", "web.nav.ussd", None), ("/about", "web.nav.about", None)]


def wants_html(request: Request) -> bool:
    """Browsers ask for text/html; API clients and tests do not."""
    return "text/html" in request.headers.get("accept", "")


def demo_mode() -> bool:
    return os.environ.get("NWT_DEMO_MODE", "1") == "1"


def register(app: FastAPI) -> None:
    """Add the HTML pages to `app` (called at the end of create_app)."""
    templates = app.state.templates
    get_db, current_user = app.state.get_db, app.state.current_user

    def lang_of(request: Request) -> str:
        """Language from ?lang= (shareable links) or the lang cookie; English otherwise."""
        lang = request.query_params.get("lang") or request.cookies.get("lang", "en")
        return lang if lang in LANGUAGES else "en"

    def render(request: Request, name: str, user: User | None, active: str | None = None,
               status_code: int = 200, **ctx: Any):
        lang = lang_of(request)

        def tr(key: str, **kw: Any) -> str:
            return t(lang, key, **kw)
        return templates.TemplateResponse(request, name, {"t": tr, "lang": lang, "user": user, "nav": NAV,
                                                          "active": active, **ctx}, status_code=status_code)

    app.state.render = render

    # -------------------------------------------------------------- language and sign-in

    @app.get("/lang/{code}")
    def set_lang(code: str, request: Request):
        resp = RedirectResponse(request.headers.get("referer") or "/", status_code=303)
        if code in LANGUAGES:
            resp.set_cookie("lang", code, max_age=365 * 86400, samesite="lax")
        return resp

    @app.get("/login")
    def login_page(request: Request, next: str = "/", user: User | None = Depends(current_user),
                   db: Session = Depends(get_db)):
        return render(request, "login.html", user, next=safe_next(next), sent=False,
                      demo_accounts=demo_accounts(db) if demo_mode() else None)

    @app.post("/login/request")
    def login_request(request: Request, phone: str = Form(...), next: str = Form("/"),
                      user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        try:
            app.state.otp.issue(phone_hash(phone))
        except ValueError:
            return render(request, "login.html", user, next=safe_next(next), sent=False, status_code=422,
                          error="Enter a valid phone number.", demo_accounts=demo_accounts(db) if demo_mode() else None)
        return render(request, "login.html", user, next=safe_next(next), sent=True, phone=phone)

    @app.post("/login/verify")
    def login_verify(request: Request, phone: str = Form(...), otp: str = Form(...), next: str = Form("/"),
                     pu_code: str = Form(""), invite_code: str = Form(""), opt_in_sms: str = Form(""),
                     db: Session = Depends(get_db)):
        ph = phone_hash(phone)
        if not app.state.otp.check(ph, otp.strip()):
            return render(request, "login.html", None, next=safe_next(next), sent=True, phone=phone,
                          error=t(lang_of(request), "web.login.failed"), status_code=401)
        user = db.execute(select(User).where(User.phone_hash == ph)).scalar_one_or_none()
        if user is None:
            user = User(phone_hash=ph, role="citizen", opted_in_notifications=bool(opt_in_sms))
            db.add(user)
        if invite_code and invite_code == app.state.settings.observer_invite_code and user.role == "citizen":
            user.role = "observer"
        if pu_code:
            unit = services.unit_by_code(db, pu_code)
            if unit is not None:
                user.pu_id = unit.id
        db.flush()
        audit.append(db, user.role, str(user.id), "signed_in", {"pu": user.pu_id, "channel": "web"})
        db.commit()
        return signed_in(user.id, next)

    @app.get("/login/demo/{role}")
    def login_demo(role: str, next: str = "/", db: Session = Depends(get_db)):
        if not demo_mode() or role not in DEMO_PHONES:
            return RedirectResponse("/login", status_code=303)
        user = db.execute(select(User).where(User.phone_hash == phone_hash(DEMO_PHONES[role]))).scalar_one_or_none()
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return signed_in(user.id, next)

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/", status_code=303)
        resp.delete_cookie("nwt_session")
        return resp

    # -------------------------------------------------------------- dashboard and map

    @app.get("/")
    def dashboard(request: Request, layer: str = "coverage", zone: str = "",
                  user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        layer = layer if layer in LAYERS else "coverage"
        zone = zone if zone in ZONES else ""
        records = services.state_records(db, layer)
        agg = services.aggregate(db, "zone", zone) if zone else services.aggregate(db, "national")
        table = [{**r, "value_text": describe(r, layer).split(": ", 1)[1]}
                 for r in sorted(records, key=lambda r: r["name"]) if not zone or r["zone"] == zone]
        return render(request, "dashboard.html", user, active="/", layer=layer, layers=LAYERS, zone=zone,
                      zones=ZONES, agg=agg, table=table, legend=legend(layer, records),
                      map_svg=render_map(records, layer, hx=True, zone=zone or None))

    @app.get("/map.svg")
    def map_svg(layer: str = "coverage", scope: str = "national", db: Session = Depends(get_db)):
        layer = layer if layer in LAYERS else "coverage"
        svg = render_map(services.state_records(db, layer), layer)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=60"})

    @app.get("/state/{code}/panel")
    def state_panel_partial(request: Request, code: str, user: User | None = Depends(current_user),
                            db: Session = Depends(get_db)):
        code = state_code(code)
        return render(request, "_state_panel.html", user, panel=services.state_panel(db, code))

    @app.get("/state/{code}")
    def state_page(request: Request, code: str, user: User | None = Depends(current_user),
                   db: Session = Depends(get_db)):
        code = state_code(code)
        panel = services.state_panel(db, code)
        rows = [{"name": l["name"], "code": l["code"], "href": f"/lga/{l['code']}", "expected": l["expected"],
                 "digitised": l["digitised"], "verified": l["verified"]} for l in panel["lgas"]]
        return render(request, "area.html", user, title=panel["name"], agg=panel, rows=rows,
                      list_title=t(lang_of(request), "web.state.lgas"), has_real=panel["has_real_sheets"],
                      show_totals=not panel["has_real_sheets"],
                      crumbs=[("/", t(lang_of(request), "web.breadcrumb.national")), ("", panel["name"])])

    @app.get("/lga/{code}")
    def lga_page(request: Request, code: str, user: User | None = Depends(current_user),
                 db: Session = Depends(get_db)):
        units = services._units_in_scope(db, "lga", code)
        if not units:
            return render(request, "area.html", user, status_code=404, title="Not found", agg=empty_agg(),
                          rows=[], list_title="", crumbs=[("/", "Nigeria"), ("", "Not found")], has_real=False,
                          show_totals=False)
        u0 = units[0]
        sheets = services.unit_sheets(db, units)
        wards: dict[str, list[PollingUnit]] = {}
        for u in units:
            wards.setdefault(u.ra_code, []).append(u)
        rows = [{"name": us[0].ward_name, "code": f"{code}-{ra}", "href": f"/ward/{code}-{ra}",
                 **services.coverage(us, sheets)} for ra, us in sorted(wards.items())]
        agg = services.aggregate(db, "lga", code)
        lang = lang_of(request)
        return render(request, "area.html", user, title=u0.lga_name, agg=agg, rows=rows,
                      list_title=t(lang, "web.lga.wards"), has_real=u0.source == "real_redacted",
                      show_totals=u0.source == "simulated",
                      crumbs=[("/", t(lang, "web.breadcrumb.national")), (f"/state/{u0.state_code}", u0.state_name),
                              ("", u0.lga_name)])

    @app.get("/ward/{code}")
    def ward_page(request: Request, code: str, user: User | None = Depends(current_user),
                  db: Session = Depends(get_db)):
        units = services._units_in_scope(db, "ward", code)
        if not units:
            return render(request, "area.html", user, status_code=404, title="Not found", agg=empty_agg(),
                          rows=[], list_title="", crumbs=[("/", "Nigeria"), ("", "Not found")], has_real=False,
                          show_totals=False)
        u0 = units[0]
        sheets = services.unit_sheets(db, units)
        rows = [{"name": u.name, "code": u.numeric_code, "href": f"/pu/{u.code}",
                 "status": sheets[u.id].status if sheets[u.id] else None} for u in units]
        lang = lang_of(request)
        return render(request, "area.html", user, title=u0.ward_name, agg=services.aggregate(db, "ward", code),
                      rows=rows, list_title=t(lang, "web.ward.units"), has_real=u0.source == "real_redacted",
                      show_totals=u0.source == "simulated",
                      crumbs=[("/", t(lang, "web.breadcrumb.national")), (f"/state/{u0.state_code}", u0.state_name),
                              (f"/lga/{u0.state_code}-{u0.lga_code}", u0.lga_name), ("", u0.ward_name)])

    def unit_page(request: Request, code: str, db: Session):
        user = current_user(request, db, request.headers.get("authorization"))
        unit = services.unit_by_code(db, code)
        lang = lang_of(request)
        if unit is None:
            return render(request, "area.html", user, status_code=404, title="Not found", agg=empty_agg(),
                          rows=[], list_title="", crumbs=[("/", "Nigeria"), ("", "Not found")], has_real=False,
                          show_totals=False)
        sheets = db.execute(select(ResultSheet).where(ResultSheet.pu_id == unit.id)
                            .order_by(ResultSheet.id.desc())).scalars().all()
        rows = [{"name": t(lang, "web.sheet.title", id=s.id), "code": t(lang, "web.source.real") if s.source ==
                 "real_redacted" else t(lang, "web.source.sim"), "href": f"/sheet/{s.id}", "status": s.status}
                for s in sheets]
        return render(request, "area.html", user, title=f"{unit.name} ({unit.numeric_code})",
                      agg={"coverage": {"real_redacted": {"expected": 0}, "simulated": {"expected": 0}}},
                      rows=rows, list_title=t(lang, "web.unit.sheets"), has_real=False, show_totals=False,
                      crumbs=[("/", t(lang, "web.breadcrumb.national")), (f"/state/{unit.state_code}", unit.state_name),
                              (f"/lga/{unit.state_code}-{unit.lga_code}", unit.lga_name),
                              (f"/ward/{unit.state_code}-{unit.lga_code}-{unit.ra_code}", unit.ward_name),
                              ("", unit.name)])

    app.state.web_unit_page = unit_page

    # -------------------------------------------------------------- sheet page and verification

    @app.get("/sheet/{sheet_id}")
    def sheet_page(request: Request, sheet_id: int, ref: str | None = None,
                   user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        sheet = db.get(ResultSheet, sheet_id)
        if sheet is None:
            return render(request, "area.html", user, status_code=404, title="Not found", agg=empty_agg(), rows=[],
                          list_title="", crumbs=[("/", "Nigeria"), ("", "Not found")], has_real=False,
                          show_totals=False)
        flash = t(lang_of(request), "web.verify.done", ref=ref) if ref and ref.isdigit() else None
        return render(request, "sheet.html", user, s=sheet_detail(db, sheet), can_verify=can_verify(user, sheet),
                      flash=flash)

    @app.post("/sheet/{sheet_id}/verify")
    def sheet_verify(request: Request, sheet_id: int, verdict: str = Form(...), comment: str = Form(""),
                     correction_field: str = Form(""), correction_value: str = Form(""),
                     user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        sheet = db.get(ResultSheet, sheet_id)
        if sheet is None or user is None:
            return RedirectResponse(f"/login?next=/sheet/{sheet_id}", status_code=303)
        if not app.state.verify_bucket.allow(str(user.id)):
            return render(request, "sheet.html", user, s=sheet_detail(db, sheet), can_verify="no",
                          error="Too many verification attempts. Try again later.", status_code=429)
        corrections = []
        if correction_field and correction_value.strip().isdigit():
            corrections = [(correction_field, int(correction_value))]
        try:
            result = services.record_verification(db, user, sheet, verdict, comment, corrections)
            db.commit()
        except services.ServiceError as err:
            db.rollback()
            return render(request, "sheet.html", user, s=sheet_detail(db, sheet), can_verify="no",
                          error=err.message, status_code=err.status)
        return RedirectResponse(f"/sheet/{sheet_id}?ref={result.case_ref}", status_code=303)

    # -------------------------------------------------------------- review

    @app.get("/review")
    def review_page(request: Request, user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        if user is None or user.role not in ("reviewer", "admin"):
            return RedirectResponse("/login?next=/review", status_code=303)
        return render(request, "review.html", user, active="/review", queue=services.review_queue(db))

    @app.get("/review/{sheet_id}")
    def review_sheet_page(request: Request, sheet_id: int, user: User | None = Depends(current_user),
                          db: Session = Depends(get_db)):
        if user is None or user.role not in ("reviewer", "admin"):
            return RedirectResponse(f"/login?next=/review/{sheet_id}", status_code=303)
        sheet = db.get(ResultSheet, sheet_id)
        return render(request, "review_sheet.html", user, active="/review", s=sheet_detail(db, sheet),
                      reason_codes=list(services.vconfig()["reason_codes"]))

    @app.post("/review/{sheet_id}")
    async def review_submit(request: Request, sheet_id: int, user: User | None = Depends(current_user),
                            db: Session = Depends(get_db)):
        if user is None or user.role not in ("reviewer", "admin"):
            return RedirectResponse("/login?next=/review", status_code=303)
        form = await request.form()
        sheet = db.get(ResultSheet, sheet_id)
        values: dict[str, int | None] = {}
        for key, raw in form.items():
            if key.startswith("f_"):
                text = str(raw).strip()
                values[key[2:]] = int(text) if text.isdigit() else None
        try:
            services.review_sheet(db, user, sheet, values, str(form.get("decision", "")),
                                  str(form.get("reason_code", "")), str(form.get("note", "")) or None)
            db.commit()
        except services.ServiceError as err:
            db.rollback()
            return render(request, "review_sheet.html", user, s=sheet_detail(db, sheet), error=err.message,
                          reason_codes=list(services.vconfig()["reason_codes"]), status_code=err.status)
        return RedirectResponse("/review", status_code=303)

    # -------------------------------------------------------------- upload (ingest role)

    def upload_units(db: Session) -> list[dict[str, Any]]:
        """Pseudonymous real units first (those still waiting for a sheet at the top)."""
        units = db.execute(select(PollingUnit).where(PollingUnit.source == "real_redacted")
                           .order_by(PollingUnit.code)).scalars().all()
        rows = [{"code": u.code, "numeric_code": u.numeric_code, "name": u.name, "state_name": u.state_name,
                 "waiting": services.latest_sheet(db, u.id) is None} for u in units]
        return sorted(rows, key=lambda r: not r["waiting"])

    @app.get("/upload")
    def upload_page(request: Request, user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        if user is None or user.role not in ("ingest", "admin"):
            return RedirectResponse("/login?next=/upload", status_code=303)
        return render(request, "upload.html", user, units=upload_units(db))

    @app.post("/upload")
    async def upload_submit(request: Request, user: User | None = Depends(current_user),
                            db: Session = Depends(get_db)):
        if user is None or user.role not in ("ingest", "admin"):
            return RedirectResponse("/login?next=/upload", status_code=303)
        form = await request.form()
        file = form.get("file")
        data = await file.read(services.MAX_UPLOAD_BYTES + 1) if hasattr(file, "read") else b""
        try:
            sheet = services.ingest_sheet(db, user, str(form.get("pu_code", "")), data,
                                          app.state.settings.upload_dir)
            job = app.state.queue.submit(db, sheet.id)
            db.commit()
        except services.ServiceError as err:
            db.rollback()
            return render(request, "upload.html", user, units=upload_units(db), error=err.message,
                          status_code=err.status)
        app.state.queue.enqueue(job.id)
        return RedirectResponse(f"/sheet/{sheet.id}", status_code=303)

    # -------------------------------------------------------------- discrepancies, audit, tracking, text

    def discrepancies_page(request: Request, status: str | None, dtype: str | None, db: Session):
        user = current_user(request, db, request.headers.get("authorization"))
        query = urlencode({k: v for k, v in {"status": status, "type": dtype}.items() if v})
        return render(request, "discrepancies.html", user, active="/discrepancies",
                      rows=services.discrepancy_rows(db, status or None, dtype or None), types=DISCREPANCY_TYPES,
                      status=status, dtype=dtype, query=query)

    app.state.web_discrepancies_page = discrepancies_page

    @app.get("/audit")
    def audit_page(request: Request, user: User | None = Depends(current_user), db: Session = Depends(get_db)):
        entries = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(50)).scalars().all()
        return render(request, "audit.html", user, active="/audit", chain=audit.verify_chain(db), entries=entries)

    @app.get("/track")
    def track_page(request: Request, ref: str | None = None, user: User | None = Depends(current_user),
                   db: Session = Depends(get_db)):
        case = services.case_status(db, ref) if ref else None
        return render(request, "track.html", user, active="/track", ref=ref, case=case)

    @app.get("/how-it-works")
    def how_page(request: Request, user: User | None = Depends(current_user)):
        return render(request, "text_page.html", user, active="/how-it-works", prefix="web.how",
                      sections=["p1", "p2", "p3", "p4", "p5"], caveat="web.how.caveat")

    @app.get("/about")
    def about_page(request: Request, user: User | None = Depends(current_user)):
        return render(request, "text_page.html", user, active="/about", prefix="web.about",
                      sections=["does", "not", "data", "limits"], caveat=None)


# ------------------------------------------------------------------ helpers

def safe_next(next_url: str) -> str:
    """Only allow local redirects."""
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"


def signed_in(user_id: int, next_url: str) -> RedirectResponse:
    resp = RedirectResponse(safe_next(next_url), status_code=303)
    resp.set_cookie("nwt_session", make_token(user_id), httponly=True, samesite="lax")
    return resp


def state_code(code: str) -> str:
    return f"{int(code):03d}" if code.isdigit() and 1 <= int(code) <= 37 else "001"


def empty_agg() -> dict[str, Any]:
    return {"coverage": {"real_redacted": {"expected": 0}, "simulated": {"expected": 0}}}


def can_verify(user: User | None, sheet: ResultSheet) -> str:
    if user is None:
        return "sign_in"
    if user.role not in ("citizen", "observer"):
        return "no"
    if user.pu_id != sheet.pu_id:
        return "other_unit"
    if sheet.status not in DIGITISED:
        return "not_ready"
    return "yes"


def demo_accounts(db: Session) -> dict[str, str]:
    """Demo roles that exist in this database, with the unit a citizen account is registered to."""
    out = {}
    for role, phone in DEMO_PHONES.items():
        user = db.execute(select(User).where(User.phone_hash == phone_hash(phone))).scalar_one_or_none()
        if user is not None:
            unit = db.get(PollingUnit, user.pu_id) if user.pu_id else None
            out[role] = f"{user.role}, unit {unit.numeric_code}" if unit else user.role
    return out
