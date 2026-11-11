"""Evaluations that need no real labels (CLAUDE.md 10.6 simulated part, 10.9, 10.10, 10.12).

Writes results/system_metrics.json (aggregates only). Runs on CPU in about a minute.
Usage: python scripts/evaluate_system.py [--records 3000]
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import ROOT  # noqa: E402
from naijawatchtower.sim.election import inject_defect, consistent_values, simulate_reading, DEFECT_WEIGHTS  # noqa: E402
from naijawatchtower.validate.decoding import NO_CONSISTENT_READING  # noqa: E402
from naijawatchtower.validate.routing import route_sheet  # noqa: E402

FLAGGED_DEFECTS = ("tampered_digit", "sum_mismatch_entry", "overvote")
CLEAN = ("none", "close_alternative", "low_confidence", "illegible")


def discrepancy_detection(n: int, seed: int = 101) -> dict:
    """Per defect type: share sent to review by a failed hard rule, and share that opened an
    ARITHMETIC_INCONSISTENT discrepancy (no consistent reading within 3 changed fields)."""
    rng = np.random.default_rng(seed)
    per = defaultdict(Counter)
    for i in range(n):
        defect = list(DEFECT_WEIGHTS)[i % len(DEFECT_WEIGHTS)]          # balanced across types
        truth, _ = inject_defect(consistent_values(rng), defect, rng)
        reading, _ = simulate_reading(truth, defect, rng)
        d = route_sheet(reading)
        hard_fail = any(not r.passed and r.severity == "hard" for r in d.rules)
        per[defect]["n"] += 1
        per[defect]["rule_flag"] += hard_fail
        per[defect]["discrepancy"] += bool(d.decoding and d.decoding.outcome == NO_CONSISTENT_READING)
        per[defect]["review"] += d.route != "AUTO_OK"
    rows = {}
    for defect, c in per.items():
        rows[defect] = {"n": c["n"], "flagged_by_rules": round(c["rule_flag"] / c["n"], 4),
                        "discrepancy_opened": round(c["discrepancy"] / c["n"], 4),
                        "sent_to_human_review": round(c["review"] / c["n"], 4)}
    tp = sum(per[d]["rule_flag"] for d in FLAGGED_DEFECTS)
    fp = sum(per[d]["rule_flag"] for d in CLEAN)
    return {"dataset": "simulated records (numbers only)", "n_records": n, "per_defect": rows,
            "rule_flag_precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "note": "A consistent forgery (all totals edited to agree) is not caught by arithmetic checks."}


def sybil_simulation() -> dict:
    """How many fake accounts it takes to change a sheet's status, using the real status rules."""
    from naijawatchtower.api import services
    from naijawatchtower.api.db import make_sessionmaker
    from naijawatchtower.api.seed import add_sim_record, add_sim_unit, add_user
    from naijawatchtower.ocr.types import FieldReading, SheetReading
    from naijawatchtower.sim.election import make_units

    def fresh():
        tmp = tempfile.mkdtemp()
        sessions = make_sessionmaker(f"sqlite:///{tmp}/s.db")
        db = sessions()
        unit = add_sim_unit(db, make_units(np.random.default_rng(0), set())[0])
        v = consistent_values(np.random.default_rng(1))
        reading = SheetReading({k: FieldReading.of(x, 0.99, alternatives=((x, 0.99),)) for k, x in v.items()})
        sheet = add_sim_record(db, unit, reading)
        db.commit()
        return db, unit, sheet

    counter = iter(range(10**6))

    def voter(db, unit, role):
        return add_user(db, f"0806{next(counter):07d}", role, unit)

    out = {}
    for observer in (False, True):
        # correct sheet; honest citizens confirm; attackers dispute it
        db, unit, sheet = fresh()
        for _ in range(5):
            services.record_verification(db, voter(db, unit, "citizen"), sheet, "confirm")
        if observer:
            services.record_verification(db, voter(db, unit, "observer"), sheet, "confirm")
        k_disputed, burst = None, False
        for k in range(1, 11):
            services.record_verification(db, voter(db, unit, "citizen"), sheet, "dispute")
            burst = burst or any("Burst flag" in (d.note or "") for d in db.query(services.Discrepancy)
                                 .filter_by(sheet_id=sheet.id))
            if sheet.status == "DISPUTED" and k_disputed is None:
                k_disputed = k
        # wrong sheet; attackers confirm it; an honest observer (if present) disputes
        db2, unit2, sheet2 = fresh()
        if observer:
            services.record_verification(db2, voter(db2, unit2, "observer"), sheet2, "dispute")
        k_verified = None
        for k in range(1, 11):
            services.record_verification(db2, voter(db2, unit2, "citizen"), sheet2, "confirm")
            if sheet2.status == "VERIFIED":
                k_verified = k
                break
        out["with_observer" if observer else "without_observer"] = {
            "attackers_to_mark_correct_sheet_disputed": k_disputed,
            "burst_flag_raised_within_10_attackers": burst,
            "attackers_to_verify_wrong_sheet": k_verified,
            "final_status_wrong_sheet_after_10_attackers": sheet2.status}
    out["note"] = ("Disputes only send a sheet to human review; attackers cannot change figures. "
                   "Citizen registration is self-declared, so this is a signal, not proof.")
    return out


def ussd_metrics() -> dict:
    """Inputs needed for check and verify-yes, and screen lengths over every reachable screen."""
    from naijawatchtower.api.db import make_sessionmaker
    from naijawatchtower.api.seed import add_sim_record, add_sim_unit, add_user
    from naijawatchtower.api.ussd import handle_ussd, walk
    from naijawatchtower.ocr.types import FieldReading, SheetReading
    from naijawatchtower.sim.election import make_units
    import contextlib
    import shutil

    tmp = Path(tempfile.mkdtemp())
    sessions = make_sessionmaker(f"sqlite:///{tmp}/u.db")
    with sessions() as db:
        unit = add_sim_unit(db, make_units(np.random.default_rng(0), set())[0])
        v = consistent_values(np.random.default_rng(3))
        add_sim_record(db, unit, SheetReading({k: FieldReading.of(x, 0.99, alternatives=((x, 0.99),))
                                               for k, x in v.items()}))
        add_user(db, "08050000001", "citizen", unit)
        db.commit()
        text, inputs = "2", 1
        while "Does this match" not in handle_ussd(text, "08050000001", "en", db)[1]:
            text += "*1"
            inputs += 1
        lines = sum(1 for k in v if (k.startswith("party_") and v[k] is not None) or k in (
            "registered_voters", "accredited_voters", "ballots_issued", "ballots_unused", "ballots_spoiled",
            "votes_rejected", "total_valid_votes", "total_used_ballots"))

    @contextlib.contextmanager
    def fresh():
        d = Path(tempfile.mkdtemp())
        shutil.copyfile(tmp / "u.db", d / "w.db")
        maker = make_sessionmaker(f"sqlite:///{d}/w.db")
        with maker() as s:
            yield s

    lengths = []
    for lang in ("en", "pcm"):
        for phone in ("08050000001", "08059999999"):
            lengths += [len(s["message"]) for s in walk(phone, lang, fresh, max_depth=7)]
    return {"inputs_check": 1, "inputs_verify_yes": inputs + 1, "verify_lines_on_example_sheet": lines,
            "screens_walked": len(lengths), "max_screen_chars": max(lengths),
            "share_screens_within_160": round(sum(x <= 160 for x in lengths) / len(lengths), 4),
            "targets": {"check": "<= 3 inputs", "verify_yes": "<= 8 inputs"}}


def geo_metrics() -> dict:
    from naijawatchtower.api import services
    from naijawatchtower.api.db import make_sessionmaker
    from naijawatchtower.web.mapsvg import render_map
    db_path = ROOT / "data" / "demo.db"
    if not db_path.exists():
        return {"note": "run scripts/seed_demo.py first"}
    with make_sessionmaker(f"sqlite:///{db_path}")() as db:
        records = services.state_records(db, "coverage")
        times = []
        for _ in range(20):
            t = time.perf_counter()
            svg = render_map(records, "coverage")
            times.append(time.perf_counter() - t)
    return {"regions": svg.count('class="region"'), "svg_bytes": len(svg.encode()),
            "svg_gzip_bytes": len(gzip.compress(svg.encode())), "render_ms_median": round(1000 * statistics.median(times), 2),
            "budget_bytes": 300_000}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=3500)
    args = parser.parse_args()
    out = {"discrepancy_detection_simulated": discrepancy_detection(args.records),
           "sybil_simulation": sybil_simulation(), "ussd": ussd_metrics(), "geo": geo_metrics()}
    path = ROOT / "results" / "system_metrics.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
