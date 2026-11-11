"""Export accepted corrections as field-level training examples (CLAUDE.md 6).

Field-level data only, never whole sheets, and no user identifiers: each row is
(image_sha256, field, model_value, corrected_value, model_confidence).

Usage: python scripts/export_feedback.py [--db data/demo.db] [--out results/feedback.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from naijawatchtower.api.db import Correction, ExtractedField, ResultSheet, make_sessionmaker  # noqa: E402
from naijawatchtower.config import ROOT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    parser.add_argument("--out", default=str(ROOT / "results" / "feedback.jsonl"))
    args = parser.parse_args()
    rows = []
    with make_sessionmaker(f"sqlite:///{args.db}")() as db:
        for c in db.execute(select(Correction).where(Correction.resolution == "accepted")).scalars():
            sheet = db.get(ResultSheet, c.sheet_id)
            model = db.execute(select(ExtractedField).where(
                ExtractedField.sheet_id == c.sheet_id, ExtractedField.name == c.field_name,
                ExtractedField.source == "model")).scalars().first()
            rows.append({"image_sha256": sheet.image_sha256, "source": sheet.source, "field": c.field_name,
                         "model_value": model.value if model else None, "corrected_value": c.proposed_value,
                         "model_confidence": model.confidence if model else None})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{len(rows)} accepted correction(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
