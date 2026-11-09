"""Simulated records that fill the map where there are no real sheets (CLAUDE.md 3.4).

Numbers only, no images. Units use `9NN` state codes and fictional LGA/ward
names; parties are the fictional "Party A" ... "Party R". Values satisfy R1-R7
by construction unless a defect is injected. Each record also carries a
simulated `SheetReading` (confidence and alternatives) so routing, review and
constraint-aware decoding are exercised exactly as for real sheets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from naijawatchtower.config import (bottom_total_field, field_names, item_fields, max_value,
                                    party_fields, states)
from naijawatchtower.ocr.types import FieldReading, SheetReading

PARTY_NAMES = tuple(f"Party {chr(ord('A') + i)}" for i in range(len(party_fields())))

DEFECT_WEIGHTS = {
    "none": 0.65, "tampered_digit": 0.10, "sum_mismatch_entry": 0.05, "overvote": 0.05,
    "illegible": 0.05, "low_confidence": 0.05, "close_alternative": 0.05,
}

Values = dict[str, int | None]


@dataclass(frozen=True)
class SimUnit:
    """A simulated polling unit."""

    code: str            # e.g. "924-01-02-003"
    numeric_code: str    # e.g. "9240102003"
    state_number: int
    state_name: str
    zone: str
    lga_code: str
    lga_name: str
    ra_code: str
    ward_name: str
    name: str


@dataclass
class SimRecord:
    """One simulated result record: what is 'written on the sheet' plus how it was 'read'."""

    unit_code: str
    defect: str
    defect_detail: str
    truth: Values
    reading: SheetReading = field(repr=False)

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable form (readings flattened)."""
        return {"unit_code": self.unit_code, "defect": self.defect, "defect_detail": self.defect_detail,
                "truth": self.truth,
                "reading": {n: {"value": r.value, "confidence": round(r.confidence, 4), "unclear": r.unclear,
                                "alternatives": [[v, round(p, 4)] for v, p in r.alternatives]}
                            for n, r in self.reading.fields.items()}}


# --------------------------------------------------------------------------- values

def consistent_values(rng: np.random.Generator) -> Values:
    """Values that satisfy every rule R1-R7 (blank party rows are None)."""
    registered = int(rng.integers(300, 1500))
    accredited = int(registered * rng.uniform(0.25, 0.60))
    rejected = int(accredited * rng.uniform(0.0, 0.05))
    valid = accredited - rejected                                    # R7
    spoiled = int(accredited * rng.uniform(0.0, 0.03))
    used = spoiled + rejected + valid                                 # R1
    issued = registered
    unused = issued - used                                            # R4 (>= 0: used <= 0.63 * registered)

    values: Values = {n: None for n in field_names()}
    values.update(dict(zip(item_fields(), [registered, accredited, issued, unused, spoiled,
                                           rejected, valid, used])))
    n_parties = int(rng.integers(2, 6))
    rows = rng.choice(len(party_fields()), size=n_parties, replace=False)
    shares = rng.dirichlet(np.full(n_parties, 0.8))
    votes = np.floor(shares * valid).astype(int)
    votes[int(np.argmax(shares))] += valid - int(votes.sum())         # R2 exactly
    for row, v in zip(rows, votes):
        values[party_fields()[row]] = int(v)
    if rng.random() < 0.7:
        values[bottom_total_field()] = valid                          # R3
    return values


def _change_one_digit(value: int, rng: np.random.Generator) -> int:
    """`value` with one digit replaced by a different digit (stays in range, no leading zero)."""
    digits = list(str(value))
    while True:
        pos = int(rng.integers(len(digits)))
        new = str(int(rng.integers(10)))
        if new == digits[pos] or (pos == 0 and new == "0" and len(digits) > 1):
            continue
        candidate = int("".join(digits[:pos] + [new] + digits[pos + 1:]))
        if 0 <= candidate <= max_value():
            return candidate


def inject_defect(values: Values, defect: str, rng: np.random.Generator) -> tuple[Values, str]:
    """Apply a value-level defect to consistent values. Returns (new values, description)."""
    v = dict(values)
    if defect == "tampered_digit":
        filled = [n for n in party_fields() if v[n]]
        name = filled[int(rng.integers(len(filled)))]
        old = v[name]
        v[name] = _change_one_digit(int(old), rng)  # type: ignore[arg-type]
        return v, f"{name}: {old} -> {v[name]}"
    if defect == "sum_mismatch_entry":
        name = "total_valid_votes" if rng.random() < 0.5 else "total_used_ballots"
        delta = int(rng.integers(1, 21)) * (1 if rng.random() < 0.5 else -1)
        old = v[name]
        v[name] = max(0, int(old) + delta)  # type: ignore[arg-type]
        return v, f"{name}: {old} -> {v[name]}"
    if defect == "overvote":
        if rng.random() < 0.5:
            k = int(rng.integers(1, 31))
            v["ballots_issued"] = int(v["total_used_ballots"]) - k  # type: ignore[arg-type]
            v["ballots_unused"] = 0
            return v, f"total_used_ballots exceeds ballots_issued by {k}"
        k = int(rng.integers(1, 31))
        v["registered_voters"] = int(v["accredited_voters"]) - k  # type: ignore[arg-type]
        return v, f"accredited_voters exceeds registered_voters by {k}"
    return v, ""


# --------------------------------------------------------------------------- readings

def _alternatives(value: int | None, top_p: float, rng: np.random.Generator,
                  k: int = 3) -> tuple[tuple[int | None, float], ...]:
    """Top-1 reading plus up to k one-digit confusions sharing the remaining probability."""
    alts: list[tuple[int | None, float]] = [(value, top_p)]
    rest = 1.0 - top_p
    if value is None:
        return tuple(alts + [(int(rng.integers(1, 10)), rest)]) if rest > 0 else tuple(alts)
    seen = {value}
    weights = rng.dirichlet(np.ones(k)) * rest
    for w in sorted(weights, reverse=True):
        cand = _change_one_digit(value, rng) if value >= 10 or rng.random() < 0.8 else None
        if cand in seen:
            continue
        seen.add(cand)
        alts.append((cand, float(w)))
    return tuple(alts)


def simulate_reading(truth: Values, defect: str, rng: np.random.Generator) -> tuple[SheetReading, str]:
    """Simulated reader output for a record. Returns (reading, extra detail)."""
    conf = {n: float(rng.uniform(0.95, 0.999)) for n in field_names()}
    top = dict(truth)
    unclear: set[str] = set()
    detail = ""
    filled = [n for n in field_names() if truth[n] is not None]
    if defect == "illegible":
        name = filled[int(rng.integers(len(filled)))]
        conf[name] = float(rng.uniform(0.3, 0.6))
        unclear.add(name)
        if rng.random() < 0.5:
            top[name] = _change_one_digit(int(truth[name]), rng)  # type: ignore[arg-type]
        detail = f"{name} smudged"
    elif defect == "low_confidence":
        for name in rng.choice(filled, size=min(len(filled), int(rng.integers(2, 5))), replace=False):
            conf[str(name)] = float(rng.uniform(0.5, 0.85))
        detail = "several low-confidence fields"
    elif defect == "close_alternative":
        name = filled[int(rng.integers(len(filled)))]
        conf[name] = float(rng.uniform(0.55, 0.75))
        detail = f"{name} has a close second reading"
    fields: dict[str, FieldReading] = {}
    for n in field_names():
        alts = _alternatives(top[n], conf[n], rng)
        if top[n] != truth[n] and all(a[0] != truth[n] for a in alts):
            alts = alts[:-1] + ((truth[n], alts[-1][1]),)   # the true value stays a candidate
        fields[n] = FieldReading.of(top[n], conf[n], n in unclear, alts)
    return SheetReading(fields=fields, model_revision="simulated", prompt_version="simulated"), detail


# --------------------------------------------------------------------------- units and election

def make_units(rng: np.random.Generator, exclude_states: set[int], per_state: int = 3) -> list[SimUnit]:
    """About `per_state` simulated units in every state not in `exclude_states`."""
    units = []
    for state in states():
        if state["number"] in exclude_states:
            continue
        for i in range(per_state):
            lga, ra, pu = 1 + i // 2, 1 + i % 2, i + 1
            numeric = f"9{state['number']:02d}{lga:02d}{ra:02d}{pu:03d}"
            units.append(SimUnit(
                code=f"9{state['number']:02d}-{lga:02d}-{ra:02d}-{pu:03d}", numeric_code=numeric,
                state_number=state["number"], state_name=state["name"], zone=state["zone"],
                lga_code=f"{lga:02d}", lga_name=f"Synthetic LGA {lga:02d}", ra_code=f"{ra:02d}",
                ward_name=f"Synthetic Ward {lga:02d}-{ra:02d}", name=f"Synthetic Unit {pu:03d}"))
    return units


def simulate_election(seed: int = 7, exclude_states: set[int] | None = None, coverage: float = 0.8,
                      no_record_states: int = 2) -> tuple[list[SimUnit], list[SimRecord], list[int]]:
    """Generate units and records. Returns (units, records, states with units but no records)."""
    rng = np.random.default_rng(seed)
    units = make_units(rng, exclude_states or set())
    state_numbers = sorted({u.state_number for u in units})
    empty = sorted(int(s) for s in rng.choice(state_numbers, size=no_record_states, replace=False))
    eligible = [u for u in units if u.state_number not in empty]
    n_records = min(len(eligible), round(coverage * len(units)))
    chosen = rng.choice(len(eligible), size=n_records, replace=False)
    defects = list(DEFECT_WEIGHTS)
    probs = np.array(list(DEFECT_WEIGHTS.values()))
    records = []
    for idx in sorted(chosen):
        unit = eligible[int(idx)]
        defect = str(rng.choice(defects, p=probs / probs.sum()))
        truth, detail = inject_defect(consistent_values(rng), defect, rng)
        reading, read_detail = simulate_reading(truth, defect, rng)
        records.append(SimRecord(unit.code, defect, detail or read_detail, truth, reading))
    return units, records, empty


def units_to_json(units: list[SimUnit]) -> list[dict[str, Any]]:
    """JSON-serialisable unit list."""
    return [asdict(u) for u in units]
