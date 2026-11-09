"""Arithmetic rules engine (CLAUDE.md 4.5), driven by `config/rules.yaml`.

Blank cells (None) count as 0 in every sum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from naijawatchtower.config import field_names, party_fields, rules_config

Values = Mapping[str, int | None]


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one rule on one sheet."""

    rule_id: str
    severity: str  # "hard" or "soft"
    passed: bool
    message: str
    detail: str


def _v(values: Values, name: str) -> int:
    """Value of a field with blank treated as 0."""
    value = values.get(name)
    return 0 if value is None else int(value)


def _expand(terms: list[str]) -> list[str]:
    """Expand the `party_rows` shorthand into the party field names."""
    out: list[str] = []
    for term in terms:
        out.extend(party_fields() if term == "party_rows" else [term])
    return out


def _check(rule: Mapping[str, Any], values: Values) -> tuple[bool, str]:
    kind = rule["kind"]
    if kind == "sum_equals":
        terms = _expand(rule["terms"])
        total = sum(_v(values, t) for t in terms)
        target = _v(values, rule["target"])
        return total == target, f"{rule['target']}={target}, sum={total}"
    if kind == "equals_if_present":
        other = values.get(rule["field"])
        if other is None:
            return True, f"{rule['field']} blank"
        target = _v(values, rule["target"])
        return int(other) == target, f"{rule['field']}={other}, {rule['target']}={target}"
    if kind == "less_equal":
        left, right = _v(values, rule["left"]), _v(values, rule["right"])
        return left <= right, f"{rule['left']}={left}, {rule['right']}={right}"
    if kind == "range":
        bad = [n for n in field_names() if values.get(n) is not None
               and not (rule["min"] <= int(values[n]) <= rule["max"])]  # type: ignore[arg-type]
        return not bad, "out of range: " + ", ".join(bad) if bad else "all in range"
    raise ValueError(f"unknown rule kind {kind!r}")


def check_rules(values: Values, rules: list[Mapping[str, Any]] | None = None) -> list[RuleResult]:
    """Evaluate every configured rule against one sheet's values."""
    rules = rules if rules is not None else rules_config()["rules"]
    results = []
    for rule in rules:
        passed, detail = _check(rule, values)
        results.append(RuleResult(rule["id"], rule["severity"], passed, rule["message"], detail))
    return results


def hard_rules_pass(values: Values) -> bool:
    """True if every hard rule holds."""
    return all(r.passed for r in check_rules(values) if r.severity == "hard")


def failed_rules(values: Values, severity: str | None = None) -> list[RuleResult]:
    """Rules that failed, optionally filtered by severity."""
    return [r for r in check_rules(values)
            if not r.passed and (severity is None or r.severity == severity)]
