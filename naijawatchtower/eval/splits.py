"""R-dev / R-test split of real sheets, by sheet, stratified by state (CLAUDE.md 3.2)."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict


def _order_key(sheet_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{sheet_id}".encode()).hexdigest()


def make_splits(sheet_states: dict[str, str], dev_frac: float = 0.25, seed: int = 0) -> dict[str, list[str]]:
    """Split sheet ids into {'dev': [...], 'test': [...]}.

    The dev size is ceil(dev_frac * N) (at least 1). It is shared out across states in
    proportion to their sheet counts (largest remainder), and within a state the sheets
    are taken in a fixed pseudo-random order derived from `seed`.
    """
    by_state: dict[str, list[str]] = defaultdict(list)
    for sheet_id, state in sheet_states.items():
        by_state[state].append(sheet_id)
    n = len(sheet_states)
    if n == 0:
        return {"dev": [], "test": []}
    n_dev = max(1, math.ceil(dev_frac * n))
    quotas = {s: n_dev * len(ids) / n for s, ids in by_state.items()}
    alloc = {s: math.floor(q) for s, q in quotas.items()}
    for s in sorted(quotas, key=lambda s: (alloc[s] - quotas[s], s))[: n_dev - sum(alloc.values())]:
        alloc[s] += 1
    dev: list[str] = []
    for state, ids in sorted(by_state.items()):
        ordered = sorted(ids, key=lambda i: _order_key(i, seed))
        dev.extend(ordered[: alloc[state]])
    test = sorted(set(sheet_states) - set(dev))
    return {"dev": sorted(dev), "test": test}
