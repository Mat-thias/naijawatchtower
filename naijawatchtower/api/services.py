"""Business logic shared by the web API and the USSD channel (CLAUDE.md 5, 6, 8.2, 9.1).

Both channels call the same functions, so a USSD dispute creates exactly the same
verification, correction, case and audit rows as a web dispute.
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from naijawatchtower.api import audit
from naijawatchtower.api.db import (CaseRef, Correction, Discrepancy, ExtractedField, PollingUnit, ResultSheet,
                                    User, Verification, utcnow)
from naijawatchtower.api.pipeline import DIGITISED, INCONSISTENT_NOTE, open_discrepancy, value_map
from naijawatchtower.config import CONFIG_DIR, field_names, party_fields, states
from naijawatchtower.sim.election import PARTY_NAMES
from naijawatchtower.validate.rules import check_rules

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
NEEDS_HUMAN = ("NEEDS_REVIEW", "UNREADABLE")


class ServiceError(Exception):
    """A refused action, with an HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@lru_cache(maxsize=1)
def vconfig() -> dict:
    """Parsed `config/verification.yaml`."""
    return yaml.safe_load((CONFIG_DIR / "verification.yaml").read_text())


def party_label(unit: PollingUnit, field: str) -> str:
    """'Party C' for simulated units, 'Row 3' for real sheets (rule 10)."""
    idx = party_fields().index(field)
    return PARTY_NAMES[idx] if unit.source == "simulated" else f"Row {idx + 1}"


# ------------------------------------------------------------------ ingest

def clean_image(data: bytes) -> tuple[bytes, str]:
    """Validate an upload and strip metadata. Returns (bytes to store, extension).

    Files without metadata are stored byte for byte (so redacted copies keep the hash
    their cached readings are keyed by); files with EXIF/XMP are re-encoded without it.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise ServiceError(413, "too_large", "Images must be 8 MB or smaller.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            if fmt not in ("JPEG", "PNG"):
                raise ServiceError(415, "unsupported_type", "Only JPEG or PNG images are accepted.")
            has_meta = bool(img.getexif()) or any(k in img.info for k in ("exif", "xmp", "XML:com.adobe.xmp"))
            ext = "jpg" if fmt == "JPEG" else "png"
            if not has_meta:
                return data, ext
            out = io.BytesIO()
            clean = img.convert("RGB") if fmt == "JPEG" else img.copy()
            clean.save(out, format=fmt, quality=95) if fmt == "JPEG" else clean.save(out, format=fmt)
            return out.getvalue(), ext
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ServiceError(415, "not_an_image", "The file is not a readable image.") from None


def ingest_sheet(db: Session, user: User, pu_code: str, data: bytes, upload_dir: Path,
                 source: str | None = None) -> ResultSheet:
    """Store an uploaded sheet image and create a PROCESSING sheet (caller enqueues and commits)."""
    unit = unit_by_code(db, pu_code)
    if unit is None:
        raise ServiceError(404, "unknown_unit", "Unknown polling-unit code.")
    clean, ext = clean_image(data)
    sha = hashlib.sha256(clean).hexdigest()
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{sha}.{ext}"
    path.write_bytes(clean)
    sheet = ResultSheet(pu_id=unit.id, source=source or unit.source, image_path=str(path), image_sha256=sha,
                        ingest_user_id=user.id, status="PROCESSING")
    db.add(sheet)
    db.flush()
    audit.append(db, user.role, str(user.id), "sheet_uploaded", {"sheet": sheet.id, "sha256": sha, "unit": unit.code},
                 sheet.id)
    return sheet


def unit_by_code(db: Session, code: str) -> PollingUnit | None:
    """Look up a unit by text code (924-01-02-003) or 10-digit numeric code."""
    code = (code or "").strip()
    return db.execute(select(PollingUnit).where((PollingUnit.code == code) | (PollingUnit.numeric_code == code))
                      ).scalar_one_or_none()


def latest_sheet(db: Session, pu_id: int) -> ResultSheet | None:
    return db.execute(select(ResultSheet).where(ResultSheet.pu_id == pu_id).order_by(ResultSheet.id.desc())
                      ).scalars().first()


# ------------------------------------------------------------------ cases

def new_case(db: Session, sheet_id: int, kind: str) -> CaseRef:
    """A fresh 5-digit reference number."""
    while True:
        ref = f"{secrets.randbelow(90000) + 10000}"
        if db.get(CaseRef, ref) is None:
            break
    case = CaseRef(ref=ref, sheet_id=sheet_id, kind=kind,
                   status="received" if kind != "confirmation" else "resolved_no_change")
    db.add(case)
    db.flush()
    return case


def case_status(db: Session, ref: str) -> dict[str, Any] | None:
    """Public view of a case: status, last update, one-line reason. Nothing else."""
    case = db.get(CaseRef, (ref or "").strip())
    if case is None:
        return None
    reason = vconfig()["reason_codes"].get(case.reason_code or "", {}).get("public")
    if case.kind == "confirmation":
        reason = "Your confirmation is recorded."
    return {"ref": case.ref, "status": case.status, "updated_at": case.updated_at.isoformat(),
            "reason": reason or {"received": "Received.", "under_review": "A reviewer is checking it."}.get(case.status)}


# ------------------------------------------------------------------ verification

@dataclass
class VerificationResult:
    verification: Verification
    case_ref: str
    status: str


def record_verification(db: Session, user: User, sheet: ResultSheet, verdict: str, comment: str | None = None,
                        corrections: list[tuple[str, int | None]] | None = None,
                        channel: str = "web") -> VerificationResult:
    """Confirm or dispute a sheet (CLAUDE.md 6). Caller commits."""
    if user.role not in ("citizen", "observer"):
        raise ServiceError(403, "role", "Only citizens and observers can verify.")
    if verdict not in ("confirm", "dispute"):
        raise ServiceError(422, "verdict", "Verdict must be confirm or dispute.")
    if user.pu_id != sheet.pu_id:
        raise ServiceError(403, "wrong_unit", "You can only verify the result of your own registered unit.")
    if sheet.status not in DIGITISED:
        raise ServiceError(409, "not_ready", "This result is not ready to verify yet.")
    existing = db.execute(select(Verification).where(Verification.sheet_id == sheet.id,
                                                     Verification.user_id == user.id)).scalar_one_or_none()
    if existing is not None:
        raise ServiceError(409, "already_recorded", f"Already recorded. Ref {existing.case_ref}.")
    corrections = corrections or []
    for name, value in corrections:
        if name not in field_names() or (value is not None and not 0 <= value <= 9999):
            raise ServiceError(422, "bad_correction", f"Invalid correction for {name}.")
    weight = vconfig()["weights"][user.role]
    case = new_case(db, sheet.id, "confirmation" if verdict == "confirm" else "dispute")
    v = Verification(sheet_id=sheet.id, user_id=user.id, verdict=verdict, weight=weight,
                     comment=(comment or "")[:500] or None, channel=channel, case_ref=case.ref)
    db.add(v)
    db.flush()
    for name, value in corrections:
        db.add(Correction(sheet_id=sheet.id, field_name=name, proposed_value=value, proposer_user_id=user.id,
                          case_ref=case.ref))
    audit.append(db, user.role, str(user.id), f"verification_{verdict}",
                 {"sheet": sheet.id, "case": case.ref, "channel": channel, "corrections": len(corrections)}, sheet.id)
    burst_check(db, sheet)
    status = recompute_status(db, sheet)
    return VerificationResult(v, case.ref, status)


def weights(db: Session, sheet_id: int) -> tuple[int, int, bool]:
    """(confirm weight, dispute weight, any observer dispute)."""
    rows = db.execute(select(Verification, User.role).join(User, User.id == Verification.user_id)
                      .where(Verification.sheet_id == sheet_id)).all()
    confirm = sum(v.weight for v, _ in rows if v.verdict == "confirm")
    dispute = sum(v.weight for v, _ in rows if v.verdict == "dispute")
    observer_dispute = any(v.verdict == "dispute" and role == "observer" for v, role in rows)
    return confirm, dispute, observer_dispute


def recompute_status(db: Session, sheet: ResultSheet) -> str:
    """Apply the status rules. A sheet still waiting for human review cannot become VERIFIED (rule 5)."""
    cfg = vconfig()
    confirm, dispute, observer_dispute = weights(db, sheet.id)
    old = sheet.status
    if dispute >= cfg["disputed_weight_min"] or observer_dispute:
        sheet.status = "DISPUTED"
        open_discrepancy(db, sheet.id, "VERIFIER_DISPUTE",
                         "Citizens or observers report that the figures differ from the sheet at the unit.")
    elif confirm >= cfg["verified_confirm_weight_min"] and dispute == 0 and sheet.status not in NEEDS_HUMAN:
        sheet.status = "VERIFIED"
    if sheet.status != old:
        audit.append(db, "system", "status_rules", "status_changed", {"sheet": sheet.id, "from": old,
                                                                      "to": sheet.status}, sheet.id)
    return sheet.status


def burst_check(db: Session, sheet: ResultSheet) -> bool:
    """Flag (never block) a burst of verifications from new accounts on one unit."""
    b = vconfig()["burst"]
    now = utcnow()
    since = now - timedelta(minutes=b["window_minutes"])
    young = now - timedelta(hours=b["account_age_hours"])
    n = db.execute(select(func.count(Verification.id)).join(User, User.id == Verification.user_id)
                   .join(ResultSheet, ResultSheet.id == Verification.sheet_id)
                   .where(ResultSheet.pu_id == sheet.pu_id, Verification.created_at >= since,
                          User.created_at >= young)).scalar_one()
    if n >= b["min_verifications"]:
        open_discrepancy(db, sheet.id, "VERIFIER_DISPUTE",
                         f"Burst flag: {n} verifications in {b['window_minutes']} minutes from accounts under "
                         f"{b['account_age_hours']} hour old. Check for coordinated activity.")
        return True
    return False


# ------------------------------------------------------------------ review

def risk_score(db: Session, sheet: ResultSheet) -> float:
    """Higher = review first: disputes, failed hard rules, unreadable, low confidence."""
    detail = json.loads(sheet.pipeline_detail_json or "{}")
    _, dispute, observer = weights(db, sheet.id)
    score = 3.0 * dispute + (5.0 if observer else 0.0) + (4.0 if sheet.status == "UNREADABLE" else 0.0)
    score += 3.0 * sum(r.startswith("rules failed") for r in detail.get("reasons", []))
    score += 0.5 * len(detail.get("low_confidence", [])) + 0.5 * len(detail.get("unclear", []))
    return score


def review_queue(db: Session) -> list[dict[str, Any]]:
    """Sheets needing a person, highest risk first."""
    pending = set(db.execute(select(Correction.sheet_id).where(Correction.resolution == "pending")).scalars())
    sheets = db.execute(select(ResultSheet).where(
        ResultSheet.status.in_(("NEEDS_REVIEW", "UNREADABLE", "DISPUTED")) | ResultSheet.id.in_(pending))
    ).scalars().all()
    rows = []
    for s in sheets:
        unit = db.get(PollingUnit, s.pu_id)
        detail = json.loads(s.pipeline_detail_json or "{}")
        confs = [c for c in db.execute(select(ExtractedField.confidence).where(
            ExtractedField.sheet_id == s.id, ExtractedField.source == "model")).scalars() if c is not None]
        badges = []
        if any(r.startswith("rules failed") for r in detail.get("reasons", [])):
            badges.append({"key": "rules", "detail": next(r for r in detail["reasons"] if r.startswith("rules failed"))
                           .removeprefix("rules failed: ")})
        if s.status == "DISPUTED":
            badges.append({"key": "dispute", "detail": ""})
        if s.status == "UNREADABLE":
            badges.append({"key": "unreadable", "detail": ""})
        if detail.get("low_confidence"):
            badges.append({"key": "low", "n": len(detail["low_confidence"])})
        if detail.get("unclear"):
            badges.append({"key": "unclear", "n": len(detail["unclear"])})
        rows.append({"sheet_id": s.id, "unit": unit.code, "numeric_code": unit.numeric_code, "state": unit.state_name,
                     "source": s.source, "status": s.status, "outcome": s.pipeline_outcome,
                     "risk": round(risk_score(db, s), 2), "pending_corrections": s.id in pending,
                     "reasons": detail.get("reasons", []), "badges": badges,
                     "min_confidence": round(min(confs), 4) if confs else None,
                     "uploaded_at": s.uploaded_at.isoformat(timespec="minutes")})
    return sorted(rows, key=lambda r: (-r["risk"], r["sheet_id"]))


def review_sheet(db: Session, reviewer: User, sheet: ResultSheet, final_values: dict[str, int | None],
                 decision: str, reason_code: str, note: str | None = None) -> str:
    """Accept or modify a sheet's values with a mandatory reason code (CLAUDE.md 5.3, 8.2)."""
    if reviewer.role not in ("reviewer", "admin"):
        raise ServiceError(403, "role", "Only reviewers can review.")
    codes = vconfig()["reason_codes"]
    if reason_code not in codes:
        raise ServiceError(422, "reason_code", f"reason_code must be one of {sorted(codes)}")
    if decision not in ("accept", "modify"):
        raise ServiceError(422, "decision", "decision must be accept or modify")
    current = value_map(db, sheet.id)
    unknown = set(final_values) - set(field_names())
    if unknown:
        raise ServiceError(422, "fields", f"unknown fields {sorted(unknown)}")
    new_values = {**current, **final_values} if decision == "modify" else current
    changed = [n for n in field_names() if new_values[n] != current[n]]
    for n in changed:
        db.add(ExtractedField(sheet_id=sheet.id, name=n, value=new_values[n], confidence=None,
                              source="human_review", reader="human"))
        model_value = db.execute(select(ExtractedField.value).where(
            ExtractedField.sheet_id == sheet.id, ExtractedField.name == n, ExtractedField.source == "model")
        ).scalars().first()
        if model_value != new_values[n]:
            db.add(Discrepancy(sheet_id=sheet.id, type="MODEL_VS_HUMAN_MISMATCH", status="resolved",
                               note=f"{n}: reviewer changed the model reading", resolved_at=utcnow()))
    for c in db.execute(select(Correction).where(Correction.sheet_id == sheet.id,
                                                 Correction.resolution == "pending")).scalars():
        c.resolution = "accepted" if new_values.get(c.field_name) == c.proposed_value else "rejected"
        c.resolved_by, c.reason_code = reviewer.id, reason_code
    case_status_value = codes[reason_code]["case_status"]
    for case in db.execute(select(CaseRef).where(CaseRef.sheet_id == sheet.id, CaseRef.kind != "confirmation",
                                                 CaseRef.status.in_(("received", "under_review")))).scalars():
        case.status, case.reason_code, case.updated_at = case_status_value, reason_code, utcnow()
    hard_ok = all(r.passed for r in check_rules(new_values) if r.severity == "hard")
    for d in db.execute(select(Discrepancy).where(Discrepancy.sheet_id == sheet.id,
                                                  Discrepancy.status == "open")).scalars():
        if d.type == "ARITHMETIC_INCONSISTENT" and not hard_ok:
            continue
        d.status, d.resolved_at = "resolved", utcnow()
    if not hard_ok:
        open_discrepancy(db, sheet.id, "ARITHMETIC_INCONSISTENT", INCONSISTENT_NOTE)
    old = sheet.status
    sheet.status = "REVIEWED"
    audit.append(db, reviewer.role, str(reviewer.id), "review_decision",
                 {"sheet": sheet.id, "decision": decision, "reason": reason_code, "changed": changed,
                  "note_hash": hashlib.sha256((note or "").encode()).hexdigest()}, sheet.id)
    audit.append(db, "system", "review", "status_changed", {"sheet": sheet.id, "from": old, "to": "REVIEWED"},
                 sheet.id)
    recompute_status(db, sheet)
    return sheet.status


# ------------------------------------------------------------------ aggregates and map

STATUS_BUCKETS = ("PROCESSING", "DIGITISED_UNVERIFIED", "NEEDS_REVIEW", "UNREADABLE", "REVIEWED", "VERIFIED",
                  "DISPUTED")


def _units_in_scope(db: Session, scope: str, code: str | None) -> list[PollingUnit]:
    q = select(PollingUnit)
    if scope == "state":
        q = q.where(PollingUnit.state_code == code)
    elif scope == "lga":
        st, lga = (code or "").split("-")
        q = q.where(PollingUnit.state_code == st, PollingUnit.lga_code == lga)
    elif scope == "ward":
        st, lga, ra = (code or "").split("-")
        q = q.where(PollingUnit.state_code == st, PollingUnit.lga_code == lga, PollingUnit.ra_code == ra)
    elif scope == "zone":
        q = q.where(PollingUnit.zone == code)
    elif scope != "national":
        raise ServiceError(422, "scope", "scope must be national, zone, state, lga or ward")
    return list(db.execute(q.order_by(PollingUnit.code)).scalars())


def unit_sheets(db: Session, units: list[PollingUnit]) -> dict[int, ResultSheet | None]:
    return {u.id: latest_sheet(db, u.id) for u in units}


def coverage(units: list[PollingUnit], sheets: dict[int, ResultSheet | None]) -> dict[str, int]:
    """Counts for one group of units (never mixes real and simulated: callers split first)."""
    c = {"expected": len(units), "digitised": 0, "reviewed": 0, "verified": 0, "disputed": 0, "needs_review": 0,
         "unreadable": 0, "processing": 0, "no_sheet": 0}
    for u in units:
        s = sheets[u.id]
        if s is None:
            c["no_sheet"] += 1
            continue
        c["digitised"] += s.status in DIGITISED
        c["reviewed"] += s.status == "REVIEWED"
        c["verified"] += s.status == "VERIFIED"
        c["disputed"] += s.status == "DISPUTED"
        c["needs_review"] += s.status == "NEEDS_REVIEW"
        c["unreadable"] += s.status == "UNREADABLE"
        c["processing"] += s.status == "PROCESSING"
    return c


def party_totals(db: Session, units: list[PollingUnit], sheets: dict[int, ResultSheet | None]) -> dict[str, Any]:
    """Per-party totals over SIMULATED records only, three ways, never merged (rule 10)."""
    sim = [u for u in units if u.source == "simulated" and sheets[u.id] is not None]
    variants = {"verified_only": ("VERIFIED",), "verified_plus_reviewed": ("VERIFIED", "REVIEWED"),
                "all_digitised": DIGITISED}
    out: dict[str, Any] = {"party_totals_scope": "simulated_records_only"}
    for key, statuses in variants.items():
        totals = {name: 0 for name in PARTY_NAMES}
        n = 0
        for u in sim:
            s = sheets[u.id]
            if s.status not in statuses:  # type: ignore[union-attr]
                continue
            n += 1
            values = value_map(db, s.id)  # type: ignore[union-attr]
            for i, f in enumerate(party_fields()):
                totals[PARTY_NAMES[i]] += values[f] or 0
        out[key] = {"sheets": n, "totals": dict(sorted(totals.items()))}
    return out


def aggregate(db: Session, scope: str = "national", code: str | None = None) -> dict[str, Any]:
    """Coverage (real and simulated separately) and simulated-only party totals for a scope."""
    units = _units_in_scope(db, scope, code)
    sheets = unit_sheets(db, units)
    real = [u for u in units if u.source == "real_redacted"]
    sim = [u for u in units if u.source == "simulated"]
    return {"scope": scope, "code": code,
            "coverage": {"real_redacted": coverage(real, sheets), "simulated": coverage(sim, sheets)},
            **party_totals(db, units, sheets),
            "note": "Per-party totals are computed over simulated records only; real sheets never enter them."}


def state_records(db: Session, layer: str = "coverage") -> list[dict[str, Any]]:
    """One record per state/FCT for the map (no party data, rule 12)."""
    if layer not in ("coverage", "verified", "discrepancies", "accreditation"):
        raise ServiceError(422, "layer", "unknown layer")
    out = []
    for st in states():
        code = f"{st['number']:03d}"
        units = _units_in_scope(db, "state", code)
        sheets = unit_sheets(db, units)
        cov = coverage(units, sheets)
        ids = [s.id for s in sheets.values() if s is not None]
        open_d = db.execute(select(func.count(Discrepancy.id)).where(
            Discrepancy.sheet_id.in_(ids), Discrepancy.status == "open")).scalar_one() if ids else 0
        if layer == "coverage":
            value = cov["digitised"] / cov["expected"] if cov["expected"] else None
        elif layer == "verified":
            value = (cov["verified"] + cov["reviewed"]) / cov["digitised"] if cov["digitised"] else None
        elif layer == "discrepancies":
            value = open_d if cov["expected"] else None
        else:
            reg = acc = 0
            for s in sheets.values():
                if s is not None and s.status in DIGITISED:
                    v = value_map(db, s.id)
                    reg += v["registered_voters"] or 0
                    acc += v["accredited_voters"] or 0
            value = acc / reg if reg else None
        out.append({"code": code, "name": st["name"], "zone": st["zone"], "map_name": st["map_name"],
                    "layer": layer, "value": value, "expected": cov["expected"], "digitised": cov["digitised"],
                    "verified": cov["verified"], "reviewed": cov["reviewed"], "open_discrepancies": open_d,
                    "real_count": sum(u.source == "real_redacted" for u in units),
                    "simulated_count": sum(u.source == "simulated" for u in units)})
    return out


def state_panel(db: Session, code: str) -> dict[str, Any]:
    """State drill-down: coverage, LGAs with counts, party totals (simulated only)."""
    units = _units_in_scope(db, "state", code)
    sheets = unit_sheets(db, units)
    lgas: dict[str, dict[str, Any]] = {}
    for u in units:
        lg = lgas.setdefault(u.lga_code, {"code": f"{code}-{u.lga_code}", "name": u.lga_name, "units": []})
        lg["units"].append(u)
    lga_rows = [{"code": v["code"], "name": v["name"], **coverage(v["units"], sheets)}
                for _, v in sorted(lgas.items())]
    st = next(s for s in states() if f"{s['number']:03d}" == code)
    has_real = any(u.source == "real_redacted" for u in units)
    return {"code": code, "name": st["name"], "zone": st["zone"], "lgas": lga_rows,
            **aggregate(db, "state", code), "has_real_sheets": has_real}


# ------------------------------------------------------------------ discrepancies

def discrepancy_rows(db: Session, status: str | None = None, dtype: str | None = None) -> list[dict[str, Any]]:
    """Export rows: no verifier identities, no names."""
    q = select(Discrepancy, ResultSheet).join(ResultSheet, ResultSheet.id == Discrepancy.sheet_id)
    if status:
        q = q.where(Discrepancy.status == status)
    if dtype:
        q = q.where(Discrepancy.type == dtype)
    rows = []
    for d, s in db.execute(q.order_by(Discrepancy.id)).all():
        unit = db.get(PollingUnit, s.pu_id)
        failed = [r.rule_id for r in check_rules(value_map(db, s.id)) if not r.passed]
        rows.append({"discrepancy_id": d.id, "sheet_id": s.id, "unit": unit.code, "source": s.source,
                     "image_sha256": s.image_sha256 or "", "type": d.type, "status": d.status,
                     "evidence": (d.note or "") + (f" Failed rules: {', '.join(failed)}." if failed else ""),
                     "created_at": d.created_at.isoformat(),
                     "resolved_at": d.resolved_at.isoformat() if d.resolved_at else ""})
    return rows
