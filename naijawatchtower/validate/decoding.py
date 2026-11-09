"""Constraint-aware decoding (CLAUDE.md 4.6): the most probable joint reading that satisfies the hard rules.

Each field has candidate readings with probabilities (the reader's alternatives).
Best-first search over fields in a fixed order, with cost = -sum(log p) and an
admissible heuristic (the cheapest candidate of every unassigned field). A hard rule
is checked as soon as all of its fields are assigned, which prunes early. At most
`max_changes` fields may differ from their top-1 reading, and at most `max_nodes`
nodes are expanded.

The result is a *suggestion* for a human reviewer. It is never applied automatically.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Mapping

from naijawatchtower.config import field_names, max_value, party_fields
from naijawatchtower.ocr.types import FieldReading
from naijawatchtower.validate.rules import Values, check_rules

REPAIRED_SUGGESTION = "REPAIRED_SUGGESTION"
AMBIGUOUS = "AMBIGUOUS"
NO_CONSISTENT_READING = "NO_CONSISTENT_READING"
NOT_NEEDED = "NOT_NEEDED"


@dataclass
class DecodingResult:
    """Outcome of the search."""

    outcome: str
    best: Values | None = None
    second: Values | None = None
    ratio: float | None = None                 # p(best) / p(second); inf if only one found
    changed: list[str] = field(default_factory=list)
    nodes: int = 0


def _rule_fields(rule: Mapping) -> set[str]:
    kind = rule["kind"]
    if kind == "sum_equals":
        names = {rule["target"]}
        for term in rule["terms"]:
            names |= set(party_fields()) if term == "party_rows" else {term}
        return names
    if kind == "equals_if_present":
        return {rule["target"], rule["field"]}
    if kind == "less_equal":
        return {rule["left"], rule["right"]}
    return set(field_names())


def candidates(reading: FieldReading, min_p: float = 1e-6) -> list[tuple[int | None, float]]:
    """In-range candidates (value, cost = -log p), cheapest first; the top-1 reading always included."""
    out: dict[int | None, float] = {}
    pairs = list(reading.alternatives) or [(reading.value, reading.confidence)]
    if all(v != reading.value for v, _ in pairs):
        pairs.insert(0, (reading.value, reading.confidence))
    for value, p in pairs:
        if value is not None and not (0 <= value <= max_value()):
            continue
        cost = -math.log(max(p, min_p))
        out[value] = min(cost, out.get(value, math.inf))
    return sorted(out.items(), key=lambda kv: kv[1])


def constraint_decode(fields: Mapping[str, FieldReading], rules: list[Mapping] | None = None,
                      max_changes: int = 3, max_nodes: int = 50_000,
                      repair_ratio_min: float = 5.0) -> DecodingResult:
    """Search for the best and second-best readings that satisfy every hard rule."""
    from naijawatchtower.config import rules_config
    all_rules = rules if rules is not None else rules_config()["rules"]
    hard = [r for r in all_rules if r["severity"] == "hard"]
    names = list(field_names())
    top1 = {n: fields[n].value for n in names}
    if all(r.passed for r in check_rules(top1, hard)):
        return DecodingResult(NOT_NEEDED, best=top1)

    cands = [candidates(fields[n]) for n in names]
    order = {n: i for i, n in enumerate(names)}
    closes_at: dict[int, list[Mapping]] = {}
    for rule in hard:                                  # check each rule once its last field is set
        last = max(order[f] for f in _rule_fields(rule))
        closes_at.setdefault(last, []).append(rule)
    suffix_min = [0.0] * (len(names) + 1)
    for i in range(len(names) - 1, -1, -1):
        suffix_min[i] = suffix_min[i + 1] + cands[i][0][1]

    seq = 0                                            # tie-breaker: never compare value tuples
    heap: list[tuple[float, int, int, float, int, tuple]] = [(suffix_min[0], seq, 0, 0.0, 0, ())]
    counter, found = 0, []
    while heap and counter < max_nodes and len(found) < 2:
        _, _, depth, cost, changes, assigned = heapq.heappop(heap)
        counter += 1
        if depth == len(names):
            found.append((cost, assigned))
            continue
        for value, c in cands[depth]:
            changed = changes + (value != top1[names[depth]])
            if changed > max_changes:
                continue
            new = assigned + (value,)
            if depth in closes_at:
                partial = dict(zip(names, new))
                if not all(r.passed for r in check_rules(partial, closes_at[depth])):
                    continue
            g = cost + c
            seq += 1
            heapq.heappush(heap, (g + suffix_min[depth + 1], seq, depth + 1, g, changed, new))

    if not found:
        return DecodingResult(NO_CONSISTENT_READING, nodes=counter)
    best = dict(zip(names, found[0][1]))
    second = dict(zip(names, found[1][1])) if len(found) > 1 else None
    ratio = math.exp(found[1][0] - found[0][0]) if second else math.inf
    changed_fields = [n for n in names if best[n] != top1[n]]
    outcome = REPAIRED_SUGGESTION if ratio >= repair_ratio_min else AMBIGUOUS
    return DecodingResult(outcome, best, second, ratio, changed_fields, counter)
