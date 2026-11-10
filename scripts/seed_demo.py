"""Build the demo database (CLAUDE.md 11, M3).

- Simulated units and records for every state without real sheets (numbers only).
- Redacted real sheets, only if Matthias has approved them in data_redacted/manifest.json and a
  cached reading exists (data_redacted/vlm_cache/). On a fresh clone (no data_redacted/), the demo
  runs on simulated records only.
- Demo accounts and a mix of statuses: verified, disputed, reviewed, waiting for review.

Usage: python scripts/seed_demo.py [--db data/demo.db] [--seed 7]
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from naijawatchtower.api.db import ResultSheet, make_sessionmaker  # noqa: E402
from naijawatchtower.api.pipeline import apply_reading, value_map  # noqa: E402
from naijawatchtower.api.seed import (DEMO_PHONES, add_real_unit, add_sim_record, add_sim_unit,  # noqa: E402
                                      add_user)
from naijawatchtower.api import audit, services  # noqa: E402
from naijawatchtower.config import DATA_REAL, DATA_REDACTED, ROOT  # noqa: E402
from naijawatchtower.ocr.cache_reader import CacheMiss, CacheReader, image_sha256  # noqa: E402
from naijawatchtower.sim.election import simulate_election  # noqa: E402


def reviewed_real_sheets() -> list[dict]:
    """Approved redacted copies with their state, from the manifest and data_real/sheets.csv."""
    manifest_path = DATA_REDACTED / "manifest.json"
    sheets_csv = DATA_REAL / "sheets.csv"
    if not manifest_path.exists() or not sheets_csv.exists():
        return []
    manifest = json.loads(manifest_path.read_text())
    with sheets_csv.open(newline="") as f:
        states = {r["sheet_id"]: r["state_code"] for r in csv.DictReader(f)}
    out = []
    for sid, entry in sorted(manifest.items()):
        path = DATA_REDACTED / entry["file"]
        if entry.get("reviewed_by_matthias") and path.exists() and states.get(sid):
            out.append({"sheet_id": sid, "path": path, "state": int(states[sid])})
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sessions = make_sessionmaker(f"sqlite:///{db_path}")
    rng = np.random.default_rng(args.seed)
    upload_dir = ROOT / "data" / "uploads"

    real = reviewed_real_sheets()
    real_states = {r["state"] for r in real}
    units, records, empty_states = simulate_election(seed=args.seed, exclude_states=real_states)
    by_code = {}
    with sessions() as db:
        for u in units:
            by_code[u.code] = add_sim_unit(db, u)
        sheet_ids = []
        for rec in records:
            sheet_ids.append(add_sim_record(db, by_code[rec.unit_code], rec.reading).id)
        db.commit()

        # Redacted real sheets (approved copies with cached readings only).
        cache = CacheReader([DATA_REDACTED / "vlm_cache"])
        real_added, real_waiting = 0, 0
        per_state: dict[int, int] = {}
        for r in real:
            per_state[r["state"]] = per_state.get(r["state"], 0) + 1
            unit = add_real_unit(db, r["state"], per_state[r["state"]])
            try:
                reading = cache.read(r["path"])
            except CacheMiss:
                real_waiting += 1          # unit exists, sheet can be uploaded live (make demo-live, /upload)
                continue
            upload_dir.mkdir(parents=True, exist_ok=True)
            sha = image_sha256(r["path"])
            dest = upload_dir / f"{sha}{r['path'].suffix}"
            shutil.copyfile(r["path"], dest)
            sheet = ResultSheet(pu_id=unit.id, source="real_redacted", image_path=str(dest), image_sha256=sha,
                                status="PROCESSING")
            db.add(sheet)
            db.flush()
            audit.append(db, "ingest", "seed", "sheet_uploaded", {"sheet": sheet.id, "sha256": sha}, sheet.id)
            apply_reading(db, sheet, reading, "vlm")
            real_added += 1
        db.commit()

        # Demo accounts: the citizens and observer share one simulated unit whose sheet is digitised, unverified.
        digitised = db.execute(select(ResultSheet).where(ResultSheet.status == "DIGITISED_UNVERIFIED",
                                                         ResultSheet.source == "simulated")
                               .order_by(ResultSheet.id)).scalars().all()
        demo_sheet = digitised[0]
        demo_unit = db.get(type(by_code[units[0].code]), demo_sheet.pu_id)
        for role, phone in DEMO_PHONES.items():
            base_role = role.split("_")[0]
            add_user(db, phone, base_role, demo_unit if base_role in ("citizen", "observer") else None,
                     opted_in=base_role == "citizen")
        db.commit()

        # A mix of statuses on other sheets: verified by three citizens, disputed, reviewed.
        reviewer = add_user(db, DEMO_PHONES["reviewer"], "reviewer")
        n_phone = 9000000
        def citizen_for(sheet: ResultSheet):
            nonlocal n_phone
            n_phone += 1
            unit = db.get(type(demo_unit), sheet.pu_id)
            return add_user(db, f"0809{n_phone:07d}", "citizen", unit)

        others = [s for s in digitised[1:]]
        rng.shuffle(others)
        for s in others[:22]:                                    # verified
            for _ in range(3):
                services.record_verification(db, citizen_for(s), s, "confirm")
        for s in others[22:26]:                                  # disputed (with a correction)
            v = value_map(db, s.id)
            field = next(f for f in v if f.startswith("party_") and v[f])
            for _ in range(2):
                services.record_verification(db, citizen_for(s), s, "dispute", None, [(field, (v[field] or 0) + 10)])
        needs = db.execute(select(ResultSheet).where(ResultSheet.status == "NEEDS_REVIEW")
                           .order_by(ResultSheet.id)).scalars().all()
        for s in needs[: max(0, len(needs) - 8)][:6]:            # reviewed; keep at least 8 in the queue
            services.review_sheet(db, reviewer, s, {}, "accept", "confirmed_as_written")
        db.commit()

        counts = {st: db.execute(select(ResultSheet).where(ResultSheet.status == st)).scalars().all()
                  for st in services.STATUS_BUCKETS}
        print(json.dumps({"db": str(db_path), "units": len(units) + real_added, "simulated_records": len(records),
                          "redacted_real_sheets": real_added, "real_units_awaiting_upload": real_waiting,
                          "states_without_records": empty_states,
                          "statuses": {k: len(v) for k, v in counts.items()},
                          "demo_unit_for_citizen_accounts": demo_unit.numeric_code,
                          "audit_chain": audit.verify_chain(db)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
