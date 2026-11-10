"""Human labelling of real sheets (CLAUDE.md 3.2).

Two modes. Typed: Matthias types every value; `reconcile` later re-checks each
disagreement with the reader. Assisted (default since 10 Nov, D41): the model pre-fills
only the fields it is very confident about and Matthias types the rest; every label
records its provenance so the evaluation can report typed and model-assisted fields apart.

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
from typing import TYPE_CHECKING, Callable

from naijawatchtower.config import (bottom_total_field, field_names, item_fields, max_value,
                                    party_fields, state_by_number)
from naijawatchtower.ocr.types import SheetReader

if TYPE_CHECKING:
    from naijawatchtower.eval.assisted import Suggestion

ILLEGIBLE = "?"
Label = int | None | str
Ask = Callable[[str], str]
Say = Callable[[str], None]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SHEETS_HEADER = ["sheet_id", "file", "sha256", "state_code", "labelled_at", "reconciled_at", "label_mode"]


def parse_value(token: str) -> Label:
    """Parse one typed value: digits, '-' or '' for blank, '?' for illegible.

    A sum written in the cell (e.g. '5+74') is labelled with its total (79); see `is_expression`.
    """
    token = token.strip()
    if token in ("", "-"):
        return None
    if token == ILLEGIBLE:
        return ILLEGIBLE
    if is_expression(token):
        value = sum(int(part) for part in token.split("+"))
    elif not token.isdigit():
        raise ValueError(f"not a number: {token!r}")
    else:
        value = int(token)
    if value > max_value():
        raise ValueError(f"{value} is above {max_value()}")
    return value


def is_expression(token: str) -> bool:
    """True for a sum written in a cell, such as '5+74'."""
    parts = token.strip().split("+")
    return len(parts) > 1 and all(p.isdigit() for p in parts)


def expression_notes(items_line: str, party_line: str, bottom: str) -> dict[str, str]:
    """Fields whose cell held a written sum, with what was typed (kept privately with the labels)."""
    notes: dict[str, str] = {}
    for name, tok in zip(item_fields(), items_line.split()):
        if is_expression(tok):
            notes[name] = f"written as {tok}"
    for pair in party_line.split():
        if "=" in pair:
            row, value = pair.split("=", 1)
            if is_expression(value) and row.isdigit() and 1 <= int(row) <= len(party_fields()):
                notes[party_fields()[int(row) - 1]] = f"written as {value}"
    if is_expression(bottom):
        notes[bottom_total_field()] = f"written as {bottom.strip()}"
    return notes


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
    notes: dict[str, dict[str, str]] = field(default_factory=dict)    # e.g. a sum written in a cell
    provenance: dict[str, dict[str, str]] = field(default_factory=dict)  # typed | model_accepted | model_corrected

    @property
    def notes_csv(self) -> Path:
        return self.data_dir / "label_notes.csv"

    @property
    def provenance_csv(self) -> Path:
        return self.data_dir / "label_provenance.csv"

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
        if store.notes_csv.exists():
            with store.notes_csv.open(newline="") as f:
                for row in csv.DictReader(f):
                    store.notes.setdefault(row["sheet_id"], {})[row["field"]] = row["note"]
        if store.provenance_csv.exists():
            with store.provenance_csv.open(newline="") as f:
                for row in csv.DictReader(f):
                    store.provenance.setdefault(row["sheet_id"], {})[row["field"]] = row["provenance"]
        for sheet_id in store.labels:                  # sheets labelled before assisted mode were typed
            store.provenance.setdefault(sheet_id, {n: "typed" for n in store.labels[sheet_id]})
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
        tmp = self.notes_csv.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sheet_id", "field", "note"])
            for sheet_id in sorted(self.notes):
                for name, note in sorted(self.notes[sheet_id].items()):
                    writer.writerow([sheet_id, name, note])
        tmp.replace(self.notes_csv)
        tmp = self.provenance_csv.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sheet_id", "field", "provenance"])
            for sheet_id in sorted(self.provenance):
                for name in field_names():
                    if name in self.provenance[sheet_id]:
                        writer.writerow([sheet_id, name, self.provenance[sheet_id][name]])
        tmp.replace(self.provenance_csv)

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


def label_sheet(ask: Ask, say: Say, suggestion: "Suggestion | None" = None,
                default_state: int | None = None) -> tuple[int, dict[str, Label], dict[str, str]]:
    """Collect the state, all 27 labels and any notes (e.g. sums written in a cell) for one sheet.

    With a `suggestion`, confident model values are pre-filled and the rest shown as `_`
    (see naijawatchtower/eval/assisted.py for how to answer).
    """
    from naijawatchtower.eval.assisted import resolve_bottom, resolve_items, resolve_party
    resolved: dict[str, str] = {}

    def resolving(key: str, resolve: Callable[[str], str], parse: Callable[[str], object]) -> Callable[[str], object]:
        def inner(text: str) -> object:
            line = resolve(text)
            parsed = parse(line)
            resolved[key] = line
            return parsed
        return inner

    while True:
        hint = f" [Enter = {state_by_number(default_state)['name']}]" if default_state else ""
        state = _ask_until_valid(ask, say, f"State (name or number, e.g. 'Lagos' or '24'){hint}: ",
                                 lambda s: default_state if (not s.strip() and default_state) else _parse_state(s))
        say(f"  -> {state_by_number(state)['name']}")  # type: ignore[arg-type]
        labels: dict[str, Label] = {}
        if suggestion is None:
            items_q = "Items 1-8, space-separated ('-' blank, '?' illegible, a written sum like 5+74 is fine): "
            party_q = ("Party rows as row=value (e.g. '2=120 14=37'); add *0 if every other row has 0 written; "
                       "Enter if all blank: ")
            bottom_q = "Bottom 'TOTAL VALID VOTES' row (Enter or '-' if blank): "
            items_r = party_r = bottom_r = (lambda s: s)
        else:
            say(f"  Model pre-filled the fields it is very sure of; {suggestion.n_missing()} field(s) shown as _ "
                "need you. Enter accepts; type missing values in order; n=value corrects one field.")
            items_q = f"Items 1-8   [{' '.join(suggestion.items_tokens())}]: "
            party_q = f"Party rows  [{suggestion.party_line() or '(all blank)'}]: "
            bottom_q = f"Bottom total [{suggestion.bottom_token()}]: "
            items_r = lambda s: resolve_items(suggestion, s)  # noqa: E731
            party_r = lambda s: resolve_party(suggestion, s)  # noqa: E731
            bottom_r = lambda s: resolve_bottom(suggestion, s)  # noqa: E731
        labels.update(_ask_until_valid(ask, say, items_q, resolving("items", items_r, parse_items)))  # type: ignore[arg-type]
        labels.update(_ask_until_valid(ask, say, party_q, resolving("parties", party_r, parse_party_rows)))  # type: ignore[arg-type]
        labels[bottom_total_field()] = _ask_until_valid(  # type: ignore[assignment]
            ask, say, bottom_q, resolving("bottom", bottom_r, parse_value))
        notes = expression_notes(resolved.get("items", ""), resolved.get("parties", ""), resolved.get("bottom", ""))
        say(format_labels(labels))
        for name, note in notes.items():
            say(f"  note: {name} {note} -> labelled as {encode(labels[name])}")
        if ask("Save? [y = save, r = redo this sheet]: ").strip().lower() in ("y", "yes", ""):
            return state, labels, notes  # type: ignore[return-value]


def run_labelling(store: LabelStore, ask: Ask, say: Say,
                  show_image: Callable[[Path], None] | None = None, limit: int | None = None,
                  suggest: "Callable[[Path], Suggestion | None] | None" = None) -> int:
    """Label every unlabelled image (resumable). Returns the number labelled.

    `suggest` gives the model's confident values for an image (assisted mode), or None.
    """
    from naijawatchtower.eval.assisted import provenance
    todo = store.unlabelled()
    say(f"{len(todo)} image(s) to label, {len(store.labels)} already labelled.")
    done = 0
    last_state: int | None = None
    for image in todo[:limit]:
        sheet_id = store.sheet_id_for(image) or store.next_sheet_id()
        say(f"\n=== {sheet_id}: {image.name} ===")
        if show_image:
            show_image(image)
        suggestion = suggest(image) if suggest else None
        if suggest and suggestion is None:
            say("  (no model reading for this image yet: type every value)")
        state, labels, notes = label_sheet(ask, say, suggestion, last_state)
        last_state = state
        store.sheets[sheet_id] = {"sheet_id": sheet_id, "file": image.name,
                                  "sha256": sha256_file(image), "state_code": f"{state:03d}",
                                  "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "reconciled_at": "", "label_mode": "assisted" if suggestion else "typed"}
        store.labels[sheet_id] = labels
        store.notes[sheet_id] = notes
        store.provenance[sheet_id] = provenance(suggestion, labels)
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
