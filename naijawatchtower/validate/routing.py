"""Routing (CLAUDE.md 4.7): which sheets may skip human review.

AUTO_OK only if every calibrated field confidence >= field_conf_min, the unclear
list is empty and every hard rule passes. Everything else goes to a person.
Even AUTO_OK means "digitised, unverified", never "official".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from naijawatchtower.config import CONFIG_DIR
from naijawatchtower.ocr.types import SheetReading
from naijawatchtower.validate.decoding import NOT_NEEDED, DecodingResult, constraint_decode
from naijawatchtower.validate.rules import RuleResult, check_rules

AUTO_OK, NEEDS_REVIEW, UNREADABLE = "AUTO_OK", "NEEDS_REVIEW", "UNREADABLE"


@lru_cache(maxsize=1)
def thresholds() -> dict:
    """Parsed `config/thresholds.yaml`."""
    return yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text())


@dataclass
class RoutingDecision:
    """Where a sheet goes, and why."""

    route: str
    reasons: list[str] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    decoding: DecodingResult | None = None
    low_confidence: list[str] = field(default_factory=list)


def route_sheet(reading: SheetReading | None, field_conf_min: float | None = None) -> RoutingDecision:
    """Apply the rules, constraint-aware decoding (if a hard rule fails) and the routing thresholds."""
    if reading is None or not reading.json_valid:
        return RoutingDecision(UNREADABLE, ["the sheet could not be read"])
    cfg = thresholds()
    conf_min = cfg["field_conf_min"] if field_conf_min is None else field_conf_min
    rules = check_rules(reading.values())
    low = [n for n, f in reading.fields.items() if f.confidence < conf_min]
    unclear = reading.unclear_fields()
    hard_failed = [r for r in rules if r.severity == "hard" and not r.passed]
    decoding = None
    reasons = []
    if hard_failed:
        decoding = constraint_decode(reading.fields, max_changes=cfg["max_changes"],
                                     max_nodes=cfg["max_nodes"], repair_ratio_min=cfg["repair_ratio_min"])
        reasons.append("rules failed: " + ", ".join(r.rule_id for r in hard_failed))
    if low:
        reasons.append(f"{len(low)} low-confidence field(s)")
    if unclear:
        reasons.append(f"{len(unclear)} field(s) marked unclear")
    route = AUTO_OK if not reasons else NEEDS_REVIEW
    if decoding is not None and decoding.outcome == NOT_NEEDED:
        decoding = None
    return RoutingDecision(route, reasons, rules, decoding, low)
