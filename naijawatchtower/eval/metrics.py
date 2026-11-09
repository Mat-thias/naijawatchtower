"""Reader metrics on labelled real sheets (CLAUDE.md 10). Aggregates only.

Fields labelled illegible ('?') are excluded from accuracy and counted separately.
Confidence intervals: percentile bootstrap over sheets (1000 resamples, seed 0).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

from naijawatchtower.config import field_names
from naijawatchtower.eval.calibration import calibrate, ece
from naijawatchtower.eval.labelling import ILLEGIBLE, Label
from naijawatchtower.ocr.types import SheetReading
from naijawatchtower.validate.decoding import REPAIRED_SUGGESTION
from naijawatchtower.validate.routing import AUTO_OK, route_sheet


@dataclass
class SheetEval:
    """Per-sheet comparison of a reading with its labels (kept in memory, never published)."""

    correct: list[bool]          # one per scored field
    nonblank: list[bool]         # label was not blank, aligned with `correct`
    confs: list[float]           # raw confidence, aligned with `correct`
    exact: bool
    illegible: int
    reading: SheetReading
    labels: Mapping[str, Label]


def compare(reading: SheetReading, labels: Mapping[str, Label]) -> SheetEval:
    """Field-by-field comparison, skipping fields labelled illegible."""
    correct, nonblank, confs, illegible = [], [], [], 0
    for n in field_names():
        if labels.get(n) == ILLEGIBLE:
            illegible += 1
            continue
        correct.append(reading.fields[n].value == labels.get(n))
        nonblank.append(labels.get(n) is not None)
        confs.append(reading.fields[n].confidence)
    return SheetEval(correct, nonblank, confs, all(correct), illegible, reading, labels)


def bootstrap(evals: Sequence[SheetEval], stat: Callable[[Sequence[SheetEval]], float],
              n: int = 1000, seed: int = 0) -> tuple[float, float, float]:
    """(point estimate, 2.5th percentile, 97.5th percentile) of `stat` resampling sheets."""
    point = stat(evals)
    if len(evals) < 2:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    samples = [stat([evals[i] for i in rng.integers(0, len(evals), len(evals))]) for _ in range(n)]
    samples = [s for s in samples if s == s]   # drop NaN (e.g. no non-blank fields in a resample)
    return point, float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))


def field_acc(evals: Sequence[SheetEval]) -> float:
    xs = [c for e in evals for c in e.correct]
    return sum(xs) / len(xs) if xs else float("nan")


def nonblank_acc(evals: Sequence[SheetEval]) -> float:
    xs = [c for e in evals for c, nb in zip(e.correct, e.nonblank) if nb]
    return sum(xs) / len(xs) if xs else float("nan")


def blank_acc(evals: Sequence[SheetEval]) -> float:
    xs = [c for e in evals for c, nb in zip(e.correct, e.nonblank) if not nb]
    return sum(xs) / len(xs) if xs else float("nan")


def sheet_acc(evals: Sequence[SheetEval]) -> float:
    return sum(e.exact for e in evals) / len(evals) if evals else float("nan")


def ci(triple: tuple[float, float, float]) -> dict[str, float]:
    return {"value": round(triple[0], 4), "ci_low": round(triple[1], 4), "ci_high": round(triple[2], 4)}


def reader_metrics(evals: Sequence[SheetEval]) -> dict:
    """Headline reader metrics with bootstrap CIs and n."""
    secs = [e.reading.seconds for e in evals]
    return {
        "n_sheets": len(evals),
        "n_fields_scored": sum(len(e.correct) for e in evals),
        "n_fields_nonblank": sum(sum(e.nonblank) for e in evals),
        "n_fields_illegible_excluded": sum(e.illegible for e in evals),
        "field_accuracy": ci(bootstrap(evals, field_acc)),
        "nonblank_field_accuracy": ci(bootstrap(evals, nonblank_acc)),
        "blank_field_accuracy": ci(bootstrap(evals, blank_acc)),
        "sheet_exact_match": ci(bootstrap(evals, sheet_acc)),
        "json_valid_rate": round(sum(e.reading.json_valid for e in evals) / len(evals), 4) if evals else None,
        "seconds_median": round(statistics.median(secs), 2) if secs else None,
        "seconds_p95": round(float(np.percentile(secs, 95)), 2) if secs else None,
        "vram_peak_mb": round(max(e.reading.vram_peak_mb for e in evals), 0) if evals else None,
        "input_tokens_median": statistics.median(e.reading.extra.get("input_tokens", 0) for e in evals)
        if evals else None,
    }


def calibration_metrics(evals: Sequence[SheetEval], temperature: float) -> dict:
    """ECE before and after temperature scaling."""
    confs = [c for e in evals for c in e.confs]
    correct = [c for e in evals for c in e.correct]
    cal = [calibrate(e.reading, temperature) for e in evals]
    cal_confs = [cal[i].fields[n].confidence for i, e in enumerate(evals)
                 for n in field_names() if e.labels.get(n) != ILLEGIBLE]
    return {"temperature": round(temperature, 4), "n_fields": len(confs),
            "ece_before": round(ece(confs, correct), 4), "ece_after": round(ece(cal_confs, correct), 4)}


def system_metrics(evals: Sequence[SheetEval], temperature: float, conf_mins: Sequence[float]) -> dict:
    """Routing, risk-coverage and the effect of constraint-aware decoding."""
    sweep = []
    for t in conf_mins:
        auto = wrong_auto = 0
        for e in evals:
            decision = route_sheet(calibrate(e.reading, temperature), field_conf_min=t)
            if decision.route == AUTO_OK:
                auto += 1
                wrong_auto += not e.exact
        sweep.append({"field_conf_min": t, "auto_ok_share": round(auto / len(evals), 4),
                      "error_rate_among_auto_ok": round(wrong_auto / auto, 4) if auto else None,
                      "wrong_but_accepted_share": round(wrong_auto / len(evals), 4),
                      "human_review_share": round(1 - auto / len(evals), 4)})
    # Constraint-aware decoding: sheet accuracy if the suggestion were taken, vs top-1.
    with_suggestion = 0
    repaired = repaired_right = 0
    for e in evals:
        decision = route_sheet(calibrate(e.reading, temperature))
        values = e.reading.values()
        if decision.decoding is not None and decision.decoding.outcome == REPAIRED_SUGGESTION:
            repaired += 1
            values = decision.decoding.best or values
            fixed = all(values[n] == e.labels.get(n) for n in field_names() if e.labels.get(n) != ILLEGIBLE)
            repaired_right += fixed
        with_suggestion += all(values[n] == e.labels.get(n) for n in field_names()
                               if e.labels.get(n) != ILLEGIBLE)
    return {"risk_coverage": sweep,
            "sheet_exact_top1": round(sheet_acc(evals), 4),
            "sheet_exact_if_suggestions_accepted": round(with_suggestion / len(evals), 4),
            "repair_suggestions": repaired, "repair_suggestions_fully_correct": repaired_right}
