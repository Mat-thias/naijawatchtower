"""Label real sheets by typing every value, then reconcile against the reader (CLAUDE.md 3.2).

Usage:
  python scripts/label_real.py                 # label every unlabelled image (resumable)
  python scripts/label_real.py reconcile       # re-check each label the model read differently
  python scripts/label_real.py redo --sheet R003   # clear one sheet's labels so it is typed again
  python scripts/label_real.py status          # counts only
  python scripts/label_real.py --assist        # optional: pre-fill confident model values (not the default)

Typing: items 1-8 on one line ('-' blank, '?' illegible, '5+74' or '5+74=79' for a written sum);
party rows as row=value, with *0 if every other row has 0 written; then the bottom total.
Reconcile compares with the stored model readings in data_real/runs/prefill/ (no GPU needed).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import DATA_REAL  # noqa: E402
from naijawatchtower.eval.labelling import LabelStore, run_labelling, run_reconcile  # noqa: E402

PREFILL = "runs/prefill"


def open_in_viewer(image: Path) -> None:
    """Show the image without blocking the terminal.

    In a VS Code terminal (including Remote-SSH) the image opens as an editor tab on your own
    screen; otherwise the desktop's default image viewer is used.
    """
    in_vscode = os.environ.get("TERM_PROGRAM") == "vscode" or os.environ.get("VSCODE_IPC_HOOK_CLI")
    command = ["code", "--reuse-window", str(image)] if in_vscode and shutil.which("code") else ["xdg-open", str(image)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def make_suggester(data_dir: Path, min_conf: float | None):
    """Optional (--assist): confident model values from the stored readings in data_real/runs/prefill/."""
    from naijawatchtower.eval.assisted import PREFILL_MIN_CONF, Suggestion
    from naijawatchtower.ocr.cache_reader import CacheReader
    cache = CacheReader([data_dir / PREFILL])

    def suggest(image: Path):
        if cache.path_for(image) is None:
            return None
        return Suggestion.from_reading(cache.read(image), min_conf or PREFILL_MIN_CONF)
    return suggest


def reconcile_reader(data_dir: Path, name: str | None):
    """The stored model readings if present (no GPU), else a live or cache reader."""
    from naijawatchtower.ocr.cache_reader import CacheReader
    from naijawatchtower.ocr.factory import make_reader
    if name is None and (data_dir / PREFILL).exists():
        return CacheReader([data_dir / PREFILL])
    return make_reader(name or "local")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="label", choices=["label", "reconcile", "redo", "status"])
    parser.add_argument("--data-dir", type=Path, default=DATA_REAL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-viewer", action="store_true", help="do not open images")
    parser.add_argument("--reader", default=None,
                        help="reconcile against: default the stored readings; 'local' (GPU) or 'cache'")
    parser.add_argument("--sheet", help="sheet id for redo, e.g. R003")
    parser.add_argument("--assist", action="store_true", help="pre-fill confident model values (not the default)")
    parser.add_argument("--min-conf", type=float, default=None, help="with --assist: confidence needed to pre-fill")
    args = parser.parse_args()

    store = LabelStore.load(args.data_dir)
    show = None if args.no_viewer else open_in_viewer
    if args.command == "status":
        reconciled = sum(1 for r in store.sheets.values() if r.get("reconciled_at"))
        print(f"images: {len(store.image_files())}  labelled: {len(store.labels)}  "
              f"reconciled: {reconciled}  unlabelled: {len(store.unlabelled())}")
        return 0
    if args.command == "redo":
        if not args.sheet or args.sheet not in store.labels:
            print(f"no labels for sheet {args.sheet!r}; labelled: {', '.join(sorted(store.labels)) or 'none'}")
            return 1
        store.clear(args.sheet)
        print(f"Cleared the labels of {args.sheet}; run `make label` to type it again.")
        return 0
    try:
        if args.command == "label":
            n = run_labelling(store, input, print, show, args.limit,
                              suggest=make_suggester(args.data_dir, args.min_conf) if args.assist else None)
            print(f"\nLabelled {n} sheet(s). Saved to {store.labels_csv}")
        else:
            changed = run_reconcile(store, reconcile_reader(args.data_dir, args.reader), input, print, show)
            print(f"\nReconciled. {changed} label(s) changed after re-checking.")
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Everything saved so far is kept; run again to resume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
