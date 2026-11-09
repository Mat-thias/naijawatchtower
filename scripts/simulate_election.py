"""Generate the simulated records that fill the map (CLAUDE.md 3.4).

States that hold labelled real sheets (from data_real/sheets.csv, if present) get no
simulated units, so real and simulated data never share a state.
Writes data/sim/units.json, data/sim/records.json and data/sim/meta.json (git-ignored).

Usage: python scripts/simulate_election.py [--seed 7] [--exclude-states 24,34]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL, ROOT  # noqa: E402
from naijawatchtower.sim.election import PARTY_NAMES, simulate_election, units_to_json  # noqa: E402

OUT = ROOT / "data/sim"


def real_sheet_states() -> set[int]:
    """State numbers of labelled real sheets."""
    path = DATA_REAL / "sheets.csv"
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {int(r["state_code"]) for r in csv.DictReader(f) if r.get("state_code")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--exclude-states", default="", help="comma-separated; default: states of real sheets")
    args = parser.parse_args()
    exclude = ({int(s) for s in args.exclude_states.split(",") if s} if args.exclude_states
               else real_sheet_states())
    units, records, empty = simulate_election(seed=args.seed, exclude_states=exclude)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "units.json").write_text(json.dumps(units_to_json(units), indent=1))
    (OUT / "records.json").write_text(json.dumps([r.to_json() for r in records]))
    meta = {"seed": args.seed, "excluded_states": sorted(exclude), "states_without_records": empty,
            "units": len(units), "records": len(records), "party_names": PARTY_NAMES,
            "defects": dict(Counter(r.defect for r in records)),
            "label": "SIMULATED RECORDS - NOT REAL DATA"}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: meta[k] for k in ("units", "records", "excluded_states",
                                           "states_without_records", "defects")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
