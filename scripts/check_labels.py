"""Check the human labels: arithmetic rules R1-R7 and disagreement with the stored model readings.

Prints, per sheet, only rule ids, counts and field names (never values) so typos can be found
and fixed. The same check is written privately to data_real/rule_check_private.txt
(CLAUDE.md 3.2; never published).

Usage: python scripts/check_labels.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL, field_names  # noqa: E402
from naijawatchtower.eval.labelling import ILLEGIBLE, LabelStore  # noqa: E402
from naijawatchtower.ocr.cache_reader import CacheReader  # noqa: E402
from naijawatchtower.validate.rules import check_rules  # noqa: E402


def main() -> int:
    store = LabelStore.load(DATA_REAL)
    cache = CacheReader([DATA_REAL / "runs" / "prefill"])
    lines = []
    for sid in sorted(store.labels):
        labels = store.labels[sid]
        row = store.sheets[sid]
        illegible = [n for n in field_names() if labels.get(n) == ILLEGIBLE]
        values = {n: (None if v == ILLEGIBLE else v) for n, v in labels.items()}
        failed = [f"{r.rule_id}{'(soft)' if r.severity == 'soft' else ''}"
                  for r in check_rules(values) if not r.passed]
        image = store.images_dir / row["file"]
        if cache.path_for(image) is not None:
            model = cache.read(image).values()
            differ = [n for n in field_names() if labels.get(n) != ILLEGIBLE and model.get(n) != labels.get(n)]
            model_txt = f"model differs on {len(differ)}/27" + (f": {', '.join(differ)}" if differ else "")
        else:
            model_txt = "no model reading"
        mode = row.get("label_mode") or "typed"
        reconciled = "reconciled" if row.get("reconciled_at") else "not reconciled"
        lines.append(f"{sid} [{mode}, {reconciled}] rules failing: {', '.join(failed) or 'none'}"
                     f" | illegible: {len(illegible)} | {model_txt}")
    out = DATA_REAL / "rule_check_private.txt"
    out.write_text("PRIVATE: never publish. Rule check on human labels.\n" + "\n".join(lines) + "\n")
    print("\n".join(lines) if lines else "no labelled sheets yet")
    print(f"\n{len(lines)} labelled sheet(s); written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
