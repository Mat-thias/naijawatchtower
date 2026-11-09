"""Split the labelled real sheets into R-dev and R-test (CLAUDE.md 3.2).

Writes data_real/splits.json. Re-running with the same labelled set gives the same split.
Run it once, after labelling, before any tuning; never move a sheet between splits afterwards.

Usage: python scripts/split_real.py [--seed 0] [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL  # noqa: E402
from naijawatchtower.eval.labelling import LabelStore  # noqa: E402
from naijawatchtower.eval.splits import make_splits  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="overwrite an existing split")
    args = parser.parse_args()
    out = DATA_REAL / "splits.json"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists; the split is frozen. Use --force only if no tuning has happened yet.")
    store = LabelStore.load(DATA_REAL)
    states = {sid: store.sheets[sid]["state_code"] for sid in store.labels}
    splits = make_splits(states, seed=args.seed)
    out.write_text(json.dumps({"seed": args.seed, **splits}, indent=1))
    print(f"R-dev: {len(splits['dev'])} sheets, R-test: {len(splits['test'])} sheets -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
