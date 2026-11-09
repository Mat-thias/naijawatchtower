"""Make redacted display copies of real sheets (CLAUDE.md 3.3).

Usage:
  python scripts/redact.py                # draw boxes on every image that has none yet
  python scripts/redact.py --redo R003    # redraw the boxes for one sheet
  python scripts/redact.py review         # approve each copy at full size (y / n)
  python scripts/redact.py apply          # re-render every copy from the saved boxes

In the drawing window: drag to add a black box. Toolbar zoom/pan for precision
(turn the tool off again before drawing).
Keys: u = undo last box, n = save and next, s = skip, q = quit.

Black out: presiding officer and agent names, signatures, phone numbers, the header
(state/LGA/ward/unit names and codes), the unit-code overlay, the party-name column,
any unit name on the stamp outside the figure cells. Never cover a figure.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.widgets import RectangleSelector  # noqa: E402

from naijawatchtower.config import DATA_REAL, DATA_REDACTED  # noqa: E402
from naijawatchtower.eval.labelling import LabelStore, sha256_file  # noqa: E402
from naijawatchtower.redact.core import (BoxStore, Manifest, load_oriented,  # noqa: E402
                                         normalise_box, redact_image)

for key in ("keymap.save", "keymap.quit", "keymap.fullscreen", "keymap.yscale", "keymap.xscale",
            "keymap.grid", "keymap.grid_minor", "keymap.back", "keymap.forward", "keymap.home"):
    plt.rcParams[key] = []


def ensure_sheet_id(store: LabelStore, image: Path) -> str:
    """Sheet id for an image, assigning the next free one if it has none yet."""
    sheet_id = store.sheet_id_for(image)
    if sheet_id is None:
        sheet_id = store.next_sheet_id()
        store.sheets[sheet_id] = {"sheet_id": sheet_id, "file": image.name, "sha256": sha256_file(image),
                                  "state_code": "", "labelled_at": "", "reconciled_at": ""}
        store.save()
    return sheet_id


def draw_boxes(image: Path, title: str, initial: list) -> tuple[str, list]:
    """Open the drawing window. Returns (action, boxes) with action in {'next','skip','quit'}."""
    img = load_oriented(image)
    fig, ax = plt.subplots(figsize=(10, 12))
    fig.canvas.manager.set_window_title(title)
    ax.imshow(img)
    ax.set_title(f"{title}   drag = box   u = undo   n = save+next   s = skip   q = quit", fontsize=9)
    ax.set_axis_off()
    boxes: list = []
    patches: list = []
    state = {"action": "quit"}

    def add(box) -> None:
        box = normalise_box(box, img.width, img.height)
        if box[2] - box[0] < 3 or box[3] - box[1] < 3:
            return
        boxes.append(box)
        patches.append(ax.add_patch(Rectangle((box[0], box[1]), box[2] - box[0], box[3] - box[1],
                                              facecolor="black", edgecolor="red", linewidth=0.8)))
        fig.canvas.draw_idle()

    def on_select(press, release) -> None:
        add((press.xdata, press.ydata, release.xdata, release.ydata))

    def on_key(event) -> None:
        if event.key == "u" and boxes:
            boxes.pop()
            patches.pop().remove()
            fig.canvas.draw_idle()
        elif event.key in ("n", "s", "q"):
            state["action"] = {"n": "next", "s": "skip", "q": "quit"}[event.key]
            plt.close(fig)

    for box in initial:
        add(box)
    selector = RectangleSelector(ax, on_select, useblit=True, button=[1], minspanx=3, minspany=3,
                                 spancoords="pixels", interactive=False)
    fig.canvas.mpl_connect("key_press_event", on_key)
    plt.tight_layout()
    plt.show()
    del selector
    return state["action"], boxes


def cmd_draw(store: LabelStore, box_store: BoxStore, manifest: Manifest, redo: str | None) -> None:
    images = store.image_files()
    if redo:
        images = [p for p in images if store.sheet_id_for(p) == redo]
        if not images:
            sys.exit(f"no image for sheet {redo}")
    else:
        images = [p for p in images if not box_store.get(store.sheet_id_for(p) or "")]
    print(f"{len(images)} image(s) to redact.")
    for image in images:
        sheet_id = ensure_sheet_id(store, image)
        action, boxes = draw_boxes(image, f"{sheet_id} ({image.name})", box_store.get(sheet_id))
        if action == "quit":
            print("Stopped; saved work is kept.")
            return
        if action == "skip":
            continue
        if not boxes:
            print(f"{sheet_id}: no boxes drawn, not saved.")
            continue
        version = box_store.put(sheet_id, image.name, boxes)
        out = DATA_REDACTED / f"{sheet_id}.jpg"
        manifest.record(sheet_id, out.name, redact_image(image, boxes, out), version)
        print(f"{sheet_id}: {len(boxes)} box(es) -> {out}")


def cmd_apply(store: LabelStore, box_store: BoxStore, manifest: Manifest) -> None:
    for sheet_id in sorted(box_store.data):
        entry = box_store.data[sheet_id]
        out = DATA_REDACTED / f"{sheet_id}.jpg"
        sha = redact_image(store.images_dir / entry["file"], box_store.get(sheet_id), out)
        if manifest.data.get(sheet_id, {}).get("sha256") != sha:
            manifest.record(sheet_id, out.name, sha, box_store.version(sheet_id))
        print(f"{sheet_id} -> {out}")


def cmd_review(manifest: Manifest) -> None:
    todo = [s for s in sorted(manifest.data) if not manifest.data[s]["reviewed_by_matthias"]]
    print(f"{len(todo)} copy(ies) to review. y = approve, n = needs redo, q = quit.")
    for sheet_id in todo:
        path = DATA_REDACTED / manifest.data[sheet_id]["file"]
        fig, ax = plt.subplots(figsize=(10, 12))
        ax.imshow(load_oriented(path))
        ax.set_axis_off()
        ax.set_title(f"{sheet_id}: is every name, signature, phone number, code and party name hidden, "
                     "and every figure visible?  y / n / q", fontsize=9)
        answer = {"key": "q"}

        def on_key(event, answer=answer, fig=fig) -> None:
            if event.key in ("y", "n", "q"):
                answer["key"] = event.key
                plt.close(fig)

        fig.canvas.mpl_connect("key_press_event", on_key)
        plt.tight_layout()
        plt.show()
        if answer["key"] == "q":
            return
        manifest.mark_reviewed(sheet_id, answer["key"] == "y")
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"{sheet_id}: {'approved' if answer['key'] == 'y' else 'needs redo (--redo ' + sheet_id + ')'} {when}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="draw", choices=["draw", "review", "apply"])
    parser.add_argument("--redo", help="sheet id to redraw")
    args = parser.parse_args()
    store = LabelStore.load(DATA_REAL)
    box_store = BoxStore(DATA_REAL / "redaction_boxes.json")
    manifest = Manifest(DATA_REDACTED / "manifest.json")
    if args.command == "draw":
        cmd_draw(store, box_store, manifest, args.redo)
    elif args.command == "apply":
        cmd_apply(store, box_store, manifest)
    else:
        cmd_review(manifest)


if __name__ == "__main__":
    main()
