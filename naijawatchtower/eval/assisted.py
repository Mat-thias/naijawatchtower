"""Model-assisted labelling (decided by Matthias, 10 Nov 2026; see docs/ASSUMPTIONS.md D41).

The model pre-fills only the fields it is very confident about: high confidence, not
marked unclear, and no rival reading close behind. Every other field is shown as `_`
and must be read from the sheet and typed. Each final label records its provenance
(model_accepted, model_corrected, typed) so the evaluation can report them separately.

Answering a pre-filled line:
  Enter            accept the line (only when no `_` is left)
  74 12            fill the `_` fields in order
  7=74             set item 7 (or party row 7) directly; combine freely with the above
  (8 values)       items line only: replace the whole line
  !<line>          party line only: replace the whole line, old syntax (e.g. '!*0 6=120')
"""

from __future__ import annotations

from dataclasses import dataclass

from naijawatchtower.config import bottom_total_field, item_fields, party_fields
from naijawatchtower.ocr.types import FieldReading, SheetReading

MISSING = "_"
PREFILL_MIN_CONF = 0.95
MAX_RIVAL_RATIO = 0.10


def is_confident(f: FieldReading, min_conf: float = PREFILL_MIN_CONF, max_rival: float = MAX_RIVAL_RATIO) -> bool:
    """Very confident: high probability, not unclear, and no close competitor."""
    if f.unclear or f.confidence < min_conf:
        return False
    rivals = [p for v, p in f.alternatives if v != f.value]
    return not rivals or max(rivals) <= max_rival * f.confidence


def _tok(value: int | None) -> str:
    return "-" if value is None else str(value)


@dataclass
class Suggestion:
    """The model's confident values; fields not in `values` are left for the labeller."""

    values: dict[str, int | None]

    @classmethod
    def from_reading(cls, reading: SheetReading, min_conf: float = PREFILL_MIN_CONF,
                     max_rival: float = MAX_RIVAL_RATIO) -> "Suggestion":
        return cls({n: f.value for n, f in reading.fields.items() if is_confident(f, min_conf, max_rival)})

    def token(self, name: str) -> str:
        return _tok(self.values[name]) if name in self.values else MISSING

    # -- items 1-8 -----------------------------------------------------------
    def items_tokens(self) -> list[str]:
        return [self.token(n) for n in item_fields()]

    # -- party rows ----------------------------------------------------------
    def party_tokens(self) -> dict[int, str]:
        return {i: self.token(n) for i, n in enumerate(party_fields(), 1)}

    def zero_default(self) -> bool:
        """Use the '*0' form when most rows are confidently 0."""
        return sum(t == "0" for t in self.party_tokens().values()) >= len(party_fields()) // 2

    def party_line(self, tokens: dict[int, str] | None = None) -> str:
        tokens = tokens or self.party_tokens()
        if self.zero_default():
            return " ".join(["*0"] + [f"{r}={t}" for r, t in tokens.items() if t != "0"])
        return " ".join(f"{r}={t}" for r, t in tokens.items() if t != "-")

    def bottom_token(self) -> str:
        return self.token(bottom_total_field())

    def n_missing(self) -> int:
        return sum(1 for n in list(item_fields()) + list(party_fields()) + [bottom_total_field()]
                   if n not in self.values)


def _split(text: str) -> tuple[list[str], list[tuple[int, str]]]:
    from naijawatchtower.eval.labelling import is_expression
    bare, pairs = [], []
    for tok in text.split():
        if "=" in tok and not is_expression(tok):              # '5+74=79' is a written sum, not n=value
            k, v = tok.split("=", 1)
            if not k.isdigit():
                raise ValueError(f"expected n=value, got {tok!r}")
            pairs.append((int(k), v))
        else:
            bare.append(tok)
    return bare, pairs


def _fill(tokens: dict[int, str], bare: list[str], pairs: list[tuple[int, str]], what: str) -> dict[int, str]:
    out = dict(tokens)
    for k, v in pairs:
        if k not in out:
            raise ValueError(f"{what} {k} does not exist")
        out[k] = v
    gaps = [k for k, v in out.items() if v == MISSING]
    if len(bare) > len(gaps):
        raise ValueError(f"{len(bare)} value(s) typed but only {len(gaps)} {what}(s) to fill")
    for k, v in zip(gaps, bare):
        out[k] = v
    still = [k for k, v in out.items() if v == MISSING]
    if still:
        raise ValueError(f"still to read from the sheet: {what} {', '.join(map(str, still))}")
    return out


def resolve_items(suggestion: Suggestion, text: str) -> str:
    """The full items line after applying the labeller's answer."""
    base = suggestion.items_tokens()
    bare, pairs = _split(text)
    if not pairs and len(bare) == len(base) and base.count(MISSING) != len(base):
        return " ".join(bare)                                    # whole line typed
    filled = _fill(dict(enumerate(base, 1)), bare, pairs, "item")
    return " ".join(filled[i] for i in range(1, len(base) + 1))


def resolve_party(suggestion: Suggestion, text: str) -> str:
    """The party line (parse_party_rows syntax) after applying the labeller's answer."""
    if text.strip().startswith("!"):
        return text.strip()[1:]
    bare, pairs = _split(text)
    filled = _fill(suggestion.party_tokens(), bare, pairs, "row")
    return suggestion.party_line(filled)


def resolve_bottom(suggestion: Suggestion, text: str) -> str:
    if text.strip():
        return text.strip()
    if suggestion.bottom_token() == MISSING:
        raise ValueError("read the bottom total from the sheet ('-' if blank)")
    return suggestion.bottom_token()


def provenance(suggestion: Suggestion | None, labels: dict) -> dict[str, str]:
    """model_accepted / model_corrected for pre-filled fields, typed for the rest."""
    out = {}
    for name, value in labels.items():
        if suggestion is None or name not in suggestion.values:
            out[name] = "typed"
        else:
            out[name] = "model_accepted" if value == suggestion.values[name] else "model_corrected"
    return out
