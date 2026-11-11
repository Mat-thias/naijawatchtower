"""Explainability helpers for the sheet page: the arithmetic rules as equations with the
sheet's own figures, and the sheet's journey as a stepper. Nothing here invents numbers:
every value comes from the stored reading (or a reviewer's correction) and the audit log.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from naijawatchtower.api.db import AuditLog, Discrepancy, ResultSheet, Verification
from naijawatchtower.config import field_spec, party_fields, rules_config
from naijawatchtower.validate.rules import check_rules

Tr = Callable[..., str]
ITEM_NO = {f["name"]: f["item"] for f in field_spec()["items"]}


def field_label(name: str, tr: Tr) -> str:
    """'Item 8 · Total used ballot papers' for numbered items; the plain name otherwise."""
    if name in ITEM_NO:
        return f"{tr('web.eq.item', n=ITEM_NO[name])} · {tr('web.field.' + name)}"
    return tr("web.field." + name)


def equations(values: dict[str, int | None], tr: Tr) -> list[dict[str, Any]]:
    """Each rule as `lhs op term + term … = total`, with pass/fail; failures first."""
    def v(name: str) -> int:
        return values.get(name) or 0

    results = {r.rule_id: r for r in check_rules(values)}
    out = []
    for order, rule in enumerate(rules_config()["rules"]):
        res = results[rule["id"]]
        row: dict[str, Any] = {"rule_id": rule["id"], "severity": rule["severity"], "passed": res.passed,
                               "message": rule["message"], "order": order, "skipped": False}
        kind = rule["kind"]
        if kind == "sum_equals":
            target = rule["target"]
            if rule["terms"] == ["party_rows"]:
                filled = [n for n in party_fields() if values.get(n) is not None]
                terms = [(tr("web.eq.party_rows", n=len(filled)), sum(v(n) for n in party_fields()))]
            else:
                terms = [(field_label(t, tr), v(t)) for t in rule["terms"]]
            row.update(lhs=(field_label(target, tr), v(target)), op="=", terms=terms,
                       total=sum(x for _, x in terms))
        elif kind == "equals_if_present":
            if values.get(rule["field"]) is None:
                row.update(skipped=True, text=tr("web.eq.bottom_blank"))
            else:
                row.update(lhs=(field_label(rule["field"], tr), v(rule["field"])), op="=",
                           terms=[(field_label(rule["target"], tr), v(rule["target"]))],
                           total=v(rule["target"]))
        elif kind == "less_equal":
            row.update(lhs=(field_label(rule["left"], tr), v(rule["left"])), op="≤",
                       terms=[(field_label(rule["right"], tr), v(rule["right"]))], total=v(rule["right"]))
        else:
            row.update(text=tr("web.eq.range"))
        out.append(row)
    return sorted(out, key=lambda r: (r["passed"], r["severity"] != "hard", r["order"]))


def stepper(db: Session, sheet: ResultSheet, tr: Tr) -> list[dict[str, str]]:
    """Journey steps with a state each: done | current | flag | skipped | pending."""
    audit = db.execute(select(AuditLog).where(AuditLog.sheet_id == sheet.id).order_by(AuditLog.id)).scalars().all()

    def first(action_prefix: str) -> str:
        return next((a.ts[:16].replace("T", " ") for a in audit if a.action.startswith(action_prefix)), "")

    detail_failed = any(r.severity == "hard" and not r.passed
                        for r in check_rules(_model_values(db, sheet)))
    reviewed = first("review_decision")
    verdicts = db.execute(select(Verification.verdict).where(Verification.sheet_id == sheet.id)).scalars().all()
    n_confirm, n_dispute = verdicts.count("confirm"), verdicts.count("dispute")
    steps = [
        {"key": "uploaded", "state": "done", "ts": first("sheet_uploaded"),
         "title": tr("web.step.simulated" if sheet.source == "simulated" else "web.step.uploaded"), "sub": ""},
        {"key": "read", "state": "current" if sheet.status == "PROCESSING" else "done", "ts": first("job_"),
         "title": tr("web.step.read"), "sub": tr("web.step.reading") if sheet.status == "PROCESSING" else ""},
    ]
    if sheet.status != "PROCESSING":
        steps.append({"key": "checked", "state": "flag" if detail_failed else "done", "ts": first("job_"),
                      "title": tr("web.step.checked"),
                      "sub": tr("web.step.rules_failed") if detail_failed else tr("web.step.rules_ok")})
        if reviewed:
            review = {"state": "done", "ts": reviewed, "sub": tr("web.step.reviewed")}
        elif sheet.status in ("NEEDS_REVIEW", "UNREADABLE", "DISPUTED"):
            review = {"state": "current", "ts": "", "sub": tr("web.step.waiting_reviewer")}
        else:
            review = {"state": "skipped", "ts": "", "sub": tr("web.step.not_needed")}
        steps.append({"key": "review", "title": tr("web.step.review"), **review})
        people_state = ("done" if sheet.status == "VERIFIED" else "flag" if sheet.status == "DISPUTED"
                        else "current" if n_confirm + n_dispute else "pending")
        steps.append({"key": "people", "state": people_state, "ts": first("verification_"),
                      "title": tr("web.step.people"),
                      "sub": tr("web.step.people_counts", confirm=n_confirm, dispute=n_dispute)})
        discrepancies = db.execute(select(Discrepancy.status).where(
            Discrepancy.sheet_id == sheet.id, Discrepancy.type != "MODEL_VS_HUMAN_MISMATCH")).scalars().all()
        if discrepancies:
            open_ = sum(s == "open" for s in discrepancies)
            steps.append({"key": "discrepancy", "state": "flag" if open_ else "done", "ts": first("discrepancy_"),
                          "title": tr("web.step.discrepancy"),
                          "sub": tr("web.step.discrepancy_open" if open_ else "web.step.discrepancy_resolved")})
    return steps


def _model_values(db: Session, sheet: ResultSheet) -> dict[str, int | None]:
    """The values as first read (before any review), for the 'arithmetic checked' step."""
    from naijawatchtower.api.db import ExtractedField
    rows = db.execute(select(ExtractedField).where(ExtractedField.sheet_id == sheet.id,
                                                   ExtractedField.source == "model")).scalars().all()
    return {r.name: r.value for r in rows}
