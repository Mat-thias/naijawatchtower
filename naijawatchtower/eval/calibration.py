"""Confidence calibration by temperature scaling (CLAUDE.md 4.4, decision D8).

calibrated = sigmoid(logit(raw) / T), with one parameter T fitted on R-dev by
minimising the negative log-likelihood of "the field was read correctly".
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from naijawatchtower.config import CONFIG_DIR
from naijawatchtower.ocr.types import SheetReading

EPS = 1e-6
CALIBRATION_FILE = CONFIG_DIR / "vlm_calibration.json"


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def apply_temperature(p: float, temperature: float) -> float:
    """Calibrated confidence for raw confidence `p`."""
    return 1.0 / (1.0 + math.exp(-_logit(p) / temperature))


def nll(confs: Sequence[float], correct: Sequence[bool], temperature: float) -> float:
    """Mean negative log-likelihood of correctness under temperature T."""
    total = 0.0
    for p, ok in zip(confs, correct):
        q = min(max(apply_temperature(p, temperature), EPS), 1 - EPS)
        total -= math.log(q if ok else 1 - q)
    return total / max(1, len(confs))


def fit_temperature(confs: Sequence[float], correct: Sequence[bool]) -> float:
    """T in [0.05, 20] minimising NLL (golden-section search on log T)."""
    lo, hi = math.log(0.05), math.log(20.0)
    phi = (math.sqrt(5) - 1) / 2
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    for _ in range(80):
        if nll(confs, correct, math.exp(a)) < nll(confs, correct, math.exp(b)):
            hi = b
        else:
            lo = a
        a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    return math.exp((lo + hi) / 2)


def ece(confs: Sequence[float], correct: Sequence[bool], bins: int = 15) -> float:
    """Expected calibration error with equal-width bins."""
    n = len(confs)
    if n == 0:
        return float("nan")
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(confs) if (lo < p <= hi) or (b == 0 and p == 0)]
        if idx:
            acc = sum(correct[i] for i in idx) / len(idx)
            conf = sum(confs[i] for i in idx) / len(idx)
            total += len(idx) / n * abs(acc - conf)
    return total


def load_temperature(model_revision: str, prompt_version: str, path: Path = CALIBRATION_FILE) -> float:
    """Fitted T for this model and prompt, or 1.0 (no change) if none is saved."""
    if not path.exists():
        return 1.0
    data = json.loads(path.read_text())
    entry = data.get(f"{model_revision}|{prompt_version}")
    return float(entry["temperature"]) if entry else 1.0


def calibrate(reading: SheetReading, temperature: float) -> SheetReading:
    """Copy of `reading` with calibrated field confidences (alternatives keep raw probabilities)."""
    fields = {n: replace(f, confidence=apply_temperature(f.confidence, temperature))
              for n, f in reading.fields.items()}
    return replace(reading, fields=fields, extra={**reading.extra, "temperature": temperature})
