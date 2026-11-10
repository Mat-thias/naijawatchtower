"""Turn a reading into stored, checked, routed text (CLAUDE.md 4 flow, 4.6, 4.7).

Store fields → rules → constraint-aware decoding (suggestion only) → routing → status,
discrepancies, SMS stub and audit entries. A repair suggestion is never applied.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from naijawatchtower.api import audit
from naijawatchtower.api.db import (Discrepancy, ExtractedField, PollingUnit, ResultSheet, SmsOutbox, User,
                                    ValidationResult)
from naijawatchtower.config import field_names
from naijawatchtower.eval.calibration import calibrate, load_temperature
from naijawatchtower.ocr.types import SheetReading
from naijawatchtower.validate.decoding import AMBIGUOUS, NO_CONSISTENT_READING, REPAIRED_SUGGESTION
from naijawatchtower.validate.routing import AUTO_OK, UNREADABLE, route_sheet
from naijawatchtower.validate.rules import check_rules

SMS_READY = "A result for your unit is ready. Dial *123# to verify."
INCONSISTENT_NOTE = "These figures do not add up as the form requires."
DIGITISED = ("DIGITISED_UNVERIFIED", "NEEDS_REVIEW", "REVIEWED", "VERIFIED", "DISPUTED")


def open_discrepancy(db: Session, sheet_id: int, dtype: str, note: str) -> Discrepancy:
    """Create a discrepancy unless an identical open one exists."""
    existing = db.execute(select(Discrepancy).where(Discrepancy.sheet_id == sheet_id, Discrepancy.type == dtype,
                                                    Discrepancy.status == "open")).scalars().first()
    if existing:
        return existing
    d = Discrepancy(sheet_id=sheet_id, type=dtype, note=note)
    db.add(d)
    db.flush()
    audit.append(db, "system", "pipeline", "discrepancy_opened", {"sheet": sheet_id, "type": dtype}, sheet_id)
    return d


def current_values(db: Session, sheet_id: int) -> dict[str, dict[str, Any]]:
    """Effective value of every field: the latest human review if any, else the model reading."""
    rows = db.execute(select(ExtractedField).where(ExtractedField.sheet_id == sheet_id)
                      .order_by(ExtractedField.id)).scalars().all()
    model = {r.name: r for r in rows if r.source == "model"}
    human = {r.name: r for r in rows if r.source == "human_review"}        # later rows win
    suggestion = {r.name: r.value for r in rows if r.source == "repair_suggestion"}
    out: dict[str, dict[str, Any]] = {}
    for name, m in model.items():
        h = human.get(name)
        out[name] = {"value": h.value if h else m.value, "source": "human_review" if h else "model",
                     "model_value": m.value, "confidence": m.confidence, "unclear": m.unclear,
                     "suggestion": suggestion.get(name, None), "has_suggestion": name in suggestion,
                     "alternatives": json.loads(m.alternatives_json) if m.alternatives_json else []}
    return out


def value_map(db: Session, sheet_id: int) -> dict[str, int | None]:
    """Effective values only."""
    cur = current_values(db, sheet_id)
    return {n: cur[n]["value"] if n in cur else None for n in field_names()}


def apply_reading(db: Session, sheet: ResultSheet, reading: SheetReading, reader_kind: str = "vlm") -> str:
    """Store a reading for `sheet`, check and route it. Returns the new status (caller commits)."""
    t = load_temperature(reading.model_revision, reading.prompt_version)
    calibrated = calibrate(reading, t)
    db.execute(delete(ExtractedField).where(ExtractedField.sheet_id == sheet.id))
    db.execute(delete(ValidationResult).where(ValidationResult.sheet_id == sheet.id))
    for n, f in calibrated.fields.items():
        db.add(ExtractedField(sheet_id=sheet.id, name=n, value=f.value, confidence=f.confidence, unclear=f.unclear,
                              source="model", reader=reader_kind,
                              alternatives_json=json.dumps([[v, round(p, 6)] for v, p in f.alternatives])))
    decision = route_sheet(calibrated)
    for r in decision.rules or check_rules(calibrated.values()):
        db.add(ValidationResult(sheet_id=sheet.id, rule_id=r.rule_id, severity=r.severity, passed=r.passed,
                                detail=r.detail))
    detail: dict[str, Any] = {"reasons": decision.reasons, "low_confidence": decision.low_confidence,
                              "unclear": calibrated.unclear_fields(), "temperature": t}
    outcome = decision.route
    if decision.decoding is not None:
        dec = decision.decoding
        outcome += f":{dec.outcome}"
        top1 = calibrated.values()
        detail["decoding"] = {"outcome": dec.outcome, "ratio": None if dec.ratio in (None, float("inf"))
                              else round(dec.ratio, 3), "changed": dec.changed}
        if dec.outcome in (REPAIRED_SUGGESTION, AMBIGUOUS) and dec.best:
            detail["decoding"]["best"] = {n: dec.best[n] for n in field_names() if dec.best[n] != top1[n]}
            if dec.second:
                detail["decoding"]["second"] = {n: dec.second[n] for n in field_names() if dec.second[n] != top1[n]}
        if dec.outcome == REPAIRED_SUGGESTION and dec.best:
            for n in dec.changed:
                db.add(ExtractedField(sheet_id=sheet.id, name=n, value=dec.best[n], confidence=None,
                                      source="repair_suggestion", reader=reader_kind))
        if dec.outcome == NO_CONSISTENT_READING:
            open_discrepancy(db, sheet.id, "ARITHMETIC_INCONSISTENT", INCONSISTENT_NOTE)
    if decision.route == UNREADABLE:
        status = "UNREADABLE"
        open_discrepancy(db, sheet.id, "UNREADABLE", "The sheet could not be read; a reviewer must check the image.")
    elif decision.route == AUTO_OK:
        status = "DIGITISED_UNVERIFIED"
    else:
        status = "NEEDS_REVIEW"
    sheet.status = status
    sheet.pipeline_outcome = outcome
    sheet.pipeline_detail_json = json.dumps(detail)
    sheet.model_revision = reading.model_revision
    sheet.prompt_version = reading.prompt_version
    if status != "UNREADABLE":
        users = db.execute(select(User).where(User.pu_id == sheet.pu_id, User.opted_in_notifications)).scalars()
        for u in users:
            db.add(SmsOutbox(user_id=u.id, body=SMS_READY))
    audit.append(db, "system", "pipeline", "job_result",
                 {"sheet": sheet.id, "status": status, "outcome": outcome, "model": reading.model_revision},
                 sheet.id)
    return status


def unit_for(db: Session, sheet: ResultSheet) -> PollingUnit:
    return db.get(PollingUnit, sheet.pu_id)  # type: ignore[return-value]
