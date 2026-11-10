"""Human labelling of real sheets (CLAUDE.md 3.2).

Matthias types every value first; only afterwards (`reconcile`) is the reader
run and each disagreement re-checked against the image. This keeps the labels
free of anchoring on the model and catches typing mistakes.

Label values: an int, None (blank cell) or ILLEGIBLE ("?").
CSV encoding: digits, "" for blank, "?" for illegible.
All files live under the data directory (default `data_real/`, git-ignored).
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from naijawatchtower.config import (bottom_total_field, field_names, item_fields, max_value,
                                    party_fields, state_by_number)
from naijawatchtower.ocr.types import SheetReader

ILLEGIBLE = "?"
Label = int | None | str
Ask = Callable[[str], str]
Say = Callable[[str], None]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SHEETS_HEADER = ["sheet_id", "file", "sha256", "state_code", "labelled_at", "reconciled_at"]


def parse_value(token: str) -> Label:
    """Parse one typed value: digits, '-' or '' for blank, '?' for illegible."""
    token = token.strip()
    if token in ("", "-"):
        return None
    if token == ILLEGIBLE:
        return ILLEGIBLE
    if not token.isdigit():
        raise ValueError(f"not a number: {token!r}")
    value = int(token)
    if value > max_value():
        raise ValueError(f"{value} is above {max_value()}")
    return value


def parse_items(line: str) -> dict[str, Label]:
    """Parse items 1-8 typed on one line, space-separated."""
    tokens = line.split()
    names = item_fields()
    if len(tokens) != len(names):
        raise ValueError(f"expected {len(names)} values, got {len(tokens)}")
    return {name: parse_value(tok) for name, tok in zip(names, tokens)}


def parse_party_rows(line: str) -> dict[str, Label]:
    """Parse party rows typed as 'row=value' pairs. Unlisted rows are blank, or 0 if '*0' is typed."""
    tokens = line.split()
    default: Label = 0 if "*0" in tokens else None
    rows: dict[str, Label] = {name: default for name in party_fields()}
    for pair in (tok for tok in tokens if tok != "*0"):
        if "=" not in pair:
            raise ValueError(f"expected row=value, got {pair!r}")
        row_txt, value_txt = pair.split("=", 1)
        if not row_txt.isdigit() or not 1 <= int(row_txt) <= len(party_fields()):
            raise ValueError(f"row must be 1-{len(party_fields())}: {row_txt!r}")
        rows[party_fields()[int(row_txt) - 1]] = parse_value(value_txt)
    return rows


def encode(value: Label) -> str:
    """CSV encoding of a label value."""
    return "" if value is None else str(value)


def decode(text: str) -> Label:
    """Inverse of `encode`."""
    return None if text == "" else ILLEGIBLE if text == ILLEGIBLE else int(text)


def sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class LabelStore:
    """The label files under one data directory."""

    data_dir: Path
    sheets: dict[str, dict[str, str]] = field(default_factory=dict)
    labels: dict[str, dict[str, Label]] = field(default_factory=dict)

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def sheets_csv(self) -> Path:
        return self.data_dir / "sheets.csv"

    @property
    def labels_csv(self) -> Path:
        return self.data_dir / "labels.csv"

    @classmethod
    def load(cls, data_dir: Path) -> "LabelStore":
        """Load existing sheets.csv and labels.csv if present."""
        store = cls(Path(data_dir))
        if store.sheets_csv.exists():
            with store.sheets_csv.open(newline="") as f:
                store.sheets = {row["sheet_id"]: row for row in csv.DictReader(f)}
        if store.labels_csv.exists():
            with store.labels_csv.open(newline="") as f:
                for row in csv.DictReader(f):
                    store.labels.setdefault(row["sheet_id"], {})[row["field"]] = decode(row["value"])
        return store

    def save(self) -> None:
        """Rewrite both CSV files atomically."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.sheets_csv.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SHEETS_HEADER)
            writer.writeheader()
            for sheet_id in sorted(self.sheets):
                writer.writerow({k: self.sheets[sheet_id].get(k, "") for k in SHEETS_HEADER})
        tmp.replace(self.sheets_csv)
        tmp = self.labels_csv.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sheet_id", "field", "value"])
            for sheet_id in sorted(self.labels):
                for name in field_names():
                    writer.writerow([sheet_id, name, encode(self.labels[sheet_id].get(name))])
        tmp.replace(self.labels_csv)

    def image_files(self) -> list[Path]:
        """All images in `images/`, sorted by name."""
        if not self.images_dir.exists():
            return []
        return sorted(p for p in self.images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)

    def sheet_id_for(self, image: Path) -> str | None:
        """Existing sheet id for an image file, if it has one."""
        for sheet_id, row in self.sheets.items():
            if row["file"] == image.name:
                return sheet_id
        return None

    def next_sheet_id(self) -> str:
        """Next free id of the form R001, R002, ..."""
        used = {int(s[1:]) for s in self.sheets if s[1:].isdigit()}
        return f"R{(max(used) + 1) if used else 1:03d}"

    def unlabelled(self) -> list[Path]:
        """Images that have no labels yet."""
        return [p for p in self.image_files()
                if (sid := self.sheet_id_for(p)) is None or sid not in self.labels]


def _ask_until_valid(ask: Ask, say: Say, prompt: str, parse: Callable[[str], object]) -> object:
    while True:
        try:
            return parse(ask(prompt))
        except ValueError as err:
            say(f"  ! {err}. Try again.")


def _parse_state(text: str) -> int:
    """State by INEC number (1-37) or by name ('Lagos', 'cross river', 'FCT', 'Abuja'; unique prefixes work)."""
    from naijawatchtower.config import states
    text = text.strip()
    if text.isdigit():
        number = int(text)
        try:
            state_by_number(number)
        except KeyError:
            raise ValueError("state number must be 1-37") from None
        return number
    key = text.lower()
    if key in ("fct", "abuja"):
        return 37
    exact = [s for s in states() if s["name"].lower() == key]
    matches = exact or [s for s in states() if s["name"].lower().startswith(key)] if key else []
    if len(matches) != 1:
        options = ", ".join(s["name"] for s in matches) if matches else "no state matches"
        raise ValueError(f"type a state name or number 1-37 ({options})")
    return matches[0]["number"]


def format_labels(labels: dict[str, Label]) -> str:
    """Compact one-screen summary of a sheet's labels."""
    items = " ".join(encode(labels[n]) or "-" for n in item_fields())
    parties = " ".join(f"{i}={encode(labels[n])}" for i, n in enumerate(party_fields(), 1)
                       if labels[n] is not None) or "(none)"
    bottom = encode(labels[bottom_total_field()]) or "-"
    return f"  items 1-8: {items}\n  party rows: {parties}\n  bottom total: {bottom}"


def label_sheet(ask: Ask, say: Say) -> tuple[int, dict[str, Label]]:
    """Collect the state number and all 27 labels for one sheet from typed input."""
    while True:
        state = _ask_until_valid(ask, say, "State (name or number, e.g. 'Lagos' or '24'): ", _parse_state)
        say(f"  -> {state_by_number(state)['name']}")  # type: ignore[arg-type]
        labels: dict[str, Label] = {}
        labels.update(_ask_until_valid(  # type: ignore[arg-type]
            ask, say, "Items 1-8, space-separated ('-' blank, '?' illegible): ", parse_items))
        labels.update(_ask_until_valid(  # type: ignore[arg-type]
            ask, say, "Party rows as row=value (e.g. '2=120 14=37'); add *0 if every other row has 0 written; "
                      "Enter if all blank: ",
            parse_party_rows))
        labels[bottom_total_field()] = _ask_until_valid(  # type: ignore[assignment]
            ask, say, "Bottom 'TOTAL VALID VOTES' row (Enter or '-' if blank): ", parse_value)
        say(format_labels(labels))
        if ask("Save? [y = save, r = redo this sheet]: ").strip().lower() in ("y", "yes", ""):
            return state, labels  # type: ignore[return-value]


def run_labelling(store: LabelStore, ask: Ask, say: Say,
                  show_image: Callable[[Path], None] | None = None, limit: int | None = None) -> int:
    """Label every unlabelled image (resumable). Returns the number labelled."""
    todo = store.unlabelled()
    say(f"{len(todo)} image(s) to label, {len(store.labels)} already labelled.")
    done = 0
    for image in todo[:limit]:
        sheet_id = store.sheet_id_for(image) or store.next_sheet_id()
        say(f"\n=== {sheet_id}: {image.name} ===")
        if show_image:
            show_image(image)
        state, labels = label_sheet(ask, say)
        store.sheets[sheet_id] = {"sheet_id": sheet_id, "file": image.name,
                                  "sha256": sha256_file(image), "state_code": f"{state:03d}",
                                  "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "reconciled_at": ""}
        store.labels[sheet_id] = labels
        store.save()
        done += 1
    return done


def run_reconcile(store: LabelStore, reader: SheetReader, ask: Ask, say: Say,
                  show_image: Callable[[Path], None] | None = None) -> int:
    """For each labelled, unreconciled sheet: read it and re-check every disagreement.

    Returns the number of labels the human changed.
    """
    changed = 0
    for sheet_id in sorted(store.labels):
        row = store.sheets[sheet_id]
        if row.get("reconciled_at"):
            continue
        image = store.images_dir / row["file"]
        model = reader.read(image).values()
        labels = store.labels[sheet_id]
        diffs = [n for n in field_names() if labels.get(n) != model.get(n)]
        say(f"\n=== {sheet_id}: {len(diffs)} disagreement(s) ===")
        if diffs and show_image:
            show_image(image)
        for name in diffs:
            prompt = (f"{name}: you typed {encode(labels[name]) or 'blank'}, the reader saw "
                      f"{encode(model.get(name)) or 'blank'}. Re-check the image. "
                      "Enter to keep yours, or type the correct value: ")
            new = _ask_until_valid(ask, say, prompt,
                                   lambda t: labels[name] if t.strip() == "" else parse_value(t))
            if new != labels[name]:
                labels[name] = new  # type: ignore[assignment]
                changed += 1
        row["reconciled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        store.save()
    return changed
