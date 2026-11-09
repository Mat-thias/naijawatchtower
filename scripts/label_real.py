"""Label real sheets by hand, then reconcile against the reader (CLAUDE.md 3.2).

Usage:
  python scripts/label_real.py                 # label every unlabelled image (resumable)
  python scripts/label_real.py --limit 5       # label at most 5 now
  python scripts/label_real.py reconcile       # after M1: re-check each disagreement with the reader
  python scripts/label_real.py status          # counts only

Images: data_real/images/ (downloaded by hand in a browser). Output stays in data_real/.
Typing: items 1-8 on one line ('-' blank, '?' illegible); party rows as row=value; bottom total.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL  # noqa: E402
from naijawatchtower.eval.labelling import LabelStore, run_labelling, run_reconcile  # noqa: E402


def open_in_viewer(image: Path) -> None:
    """Open the image in the system viewer without blocking the terminal."""
    subprocess.Popen(["xdg-open", str(image)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="label", choices=["label", "reconcile", "status"])
    parser.add_argument("--data-dir", type=Path, default=DATA_REAL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-viewer", action="store_true", help="do not open images")
    parser.add_argument("--reader", default=os.environ.get("VLM_BACKEND", "local"),
                        help="reader for reconcile: local (GPU) or cache")
    args = parser.parse_args()

    store = LabelStore.load(args.data_dir)
    show = None if args.no_viewer else open_in_viewer
    if args.command == "status":
        reconciled = sum(1 for r in store.sheets.values() if r.get("reconciled_at"))
        print(f"images: {len(store.image_files())}  labelled: {len(store.labels)}  "
              f"reconciled: {reconciled}  unlabelled: {len(store.unlabelled())}")
        return 0
    try:
        if args.command == "label":
            n = run_labelling(store, input, print, show, args.limit)
            print(f"\nLabelled {n} sheet(s). Saved to {store.labels_csv}")
        else:
            from naijawatchtower.ocr.factory import make_reader
            changed = run_reconcile(store, make_reader(args.reader), input, print, show)
            print(f"\nReconciled. {changed} label(s) changed after re-checking.")
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Everything saved so far is kept; run again to resume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
