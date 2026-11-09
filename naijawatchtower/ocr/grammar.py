"""Constrained decoding for the fixed output schema (CLAUDE.md 4.2, 4.4).

The model never writes free text. The JSON skeleton is forced token by token:

  {"fields":{"registered_voters":<V>,"accredited_voters":<V>, ... ,"total_valid_votes_bottom":<V>},"unclear":[<U>]}

At each <V> the model may choose only a digit, `null`, or (after at least one digit)
the first token of the next skeleton segment, which ends the number. At <U> it may
choose only quoted field names or the closing bracket. The output therefore always
parses, can contain nothing but the 27 numbers and field names, and text written on
a sheet cannot change its structure.

Everything here is tokenizer-agnostic (it only needs `encode`), so it is unit-tested
with a character-level fake tokenizer and no model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

from naijawatchtower.ocr.types import FieldReading

Encode = Callable[[str], list[int]]
MAX_DIGITS = 4
TOP_K = 5


def _trie(options: dict[str, list[int]]) -> dict:
    """Trie over token sequences: node = {'children': {tok: node}, 'end': option or None}."""
    root: dict = {"children": {}, "end": None}
    for option, toks in options.items():
        node = root
        for tok in toks:
            node = node["children"].setdefault(tok, {"children": {}, "end": None})
        node["end"] = option
    return root


@dataclass
class StepInfo:
    """What one generated token was: part of a field's value choice, forced, or the unclear list."""

    role: str                 # "value" | "forced" | "unclear" | "eos"
    field: str | None = None


@dataclass
class OutputGrammar:
    """Token-level grammar for the output schema, built from an `encode` function."""

    encode: Encode
    field_names: Sequence[str]
    eos_id: int
    digit_ids: dict[int, int] = field(init=False)     # token id -> digit
    null_ids: list[int] = field(init=False)
    segments: list[list[int]] = field(init=False)     # segments[i] precedes value i; last closes fields
    first_trie: dict = field(init=False)
    next_trie: dict = field(init=False)

    def __post_init__(self) -> None:
        self.digit_ids = {}
        for d in range(10):
            toks = self.encode(str(d))
            if len(toks) != 1:
                raise ValueError(f"digit {d} is not a single token; constrained decoding needs that")
            self.digit_ids[toks[0]] = d
        self.null_ids = self.encode("null")
        names = list(self.field_names)
        texts = ['{"fields":{"' + names[0] + '":']
        texts += [',"' + n + '":' for n in names[1:]]
        texts.append('},"unclear":[')
        self.segments = [self.encode(t) for t in texts]
        for seg in self.segments[1:]:
            if seg[0] in self.digit_ids or seg[0] == self.null_ids[0]:
                raise ValueError("a segment starts with a digit or null token")
        close = self.encode("]}")
        self.first_trie = _trie({**{n: self.encode(f'"{n}"') for n in names}, "]}": close})
        self.next_trie = _trie({**{n: self.encode(f',"{n}"') for n in names}, "]}": close})

    @property
    def max_new_tokens(self) -> int:
        """Upper bound on generated tokens for one sheet."""
        unclear = max(len(self.encode(f',"{n}"')) for n in self.field_names) * len(self.field_names)
        return sum(map(len, self.segments)) + len(self.field_names) * (MAX_DIGITS + len(self.null_ids)) \
            + unclear + 8

    def machine(self) -> "GrammarMachine":
        """A fresh state machine for one generation."""
        return GrammarMachine(self)


class GrammarMachine:
    """Incremental state of one constrained generation."""

    def __init__(self, g: OutputGrammar) -> None:
        self.g = g
        self.mode = "forced"          # forced | value | null_rest | unclear | done
        self.seg = 0                  # current segment index
        self.pos = 0                  # position within segment / null tokens
        self.field_idx = 0            # index of the value being read
        self.n_digits = 0
        self.first_digit_zero = False
        self.trie: dict = g.first_trie
        self.node: dict = g.first_trie
        self.unclear: list[str] = []
        self.trace: list[StepInfo] = []
        self.tokens: list[int] = []

    # -- what may come next -------------------------------------------------
    def allowed(self) -> list[int]:
        """Token ids allowed at the current step."""
        g = self.g
        if self.mode == "forced":
            return [g.segments[self.seg][self.pos]]
        if self.mode == "null_rest":
            return [g.null_ids[self.pos]]
        if self.mode == "value":
            terminator = g.segments[self.field_idx + 1][0]
            if self.n_digits == 0:
                return list(g.digit_ids) + [g.null_ids[0]]
            if self.n_digits >= MAX_DIGITS or self.first_digit_zero:
                return [terminator]
            return list(g.digit_ids) + [terminator]
        if self.mode == "unclear":
            if len(self.unclear) >= len(g.field_names) and self.node is self.trie:
                return [g.encode("]}")[0]]
            return list(self.node["children"])
        return [g.eos_id]

    def role(self) -> StepInfo:
        """Role of the step about to be taken."""
        if self.mode == "value":
            return StepInfo("value", self.g.field_names[self.field_idx])
        if self.mode in ("forced", "null_rest"):
            return StepInfo("forced")
        if self.mode == "unclear":
            return StepInfo("unclear")
        return StepInfo("eos")

    @property
    def done(self) -> bool:
        return self.mode == "done"

    # -- take a step --------------------------------------------------------
    def advance(self, tok: int) -> None:
        """Consume one generated token (must be allowed)."""
        allowed = self.allowed()
        if tok not in allowed:
            raise ValueError(f"token {tok} not allowed in mode {self.mode}")
        self.trace.append(self.role())
        self.tokens.append(tok)
        g = self.g
        if self.mode == "forced":
            self.pos += 1
            if self.pos == len(g.segments[self.seg]):
                self._after_segment()
        elif self.mode == "null_rest":
            self.pos += 1
            if self.pos == len(g.null_ids):
                self._end_value(consumed_terminator=False)
        elif self.mode == "value":
            if tok in g.digit_ids:
                self.first_digit_zero = self.n_digits == 0 and g.digit_ids[tok] == 0
                self.n_digits += 1
            elif self.n_digits == 0 and tok == g.null_ids[0]:
                if len(g.null_ids) == 1:
                    self._end_value(consumed_terminator=False)
                else:
                    self.mode, self.pos = "null_rest", 1
            else:  # terminator: first token of the next segment
                self._end_value(consumed_terminator=True)
        elif self.mode == "unclear":
            self.node = self.node["children"][tok]
            if self.node["end"] is not None and not self.node["children"]:
                option = self.node["end"]
                if option == "]}":
                    self.mode = "done"
                else:
                    self.unclear.append(option)
                    self.trie = self.node = g.next_trie
        elif self.mode == "done":
            pass

    def _after_segment(self) -> None:
        if self.seg == len(self.g.segments) - 1:
            self.mode, self.node, self.trie = "unclear", self.g.first_trie, self.g.first_trie
        else:
            self.mode, self.n_digits, self.first_digit_zero = "value", 0, False
            self.field_idx = self.seg

    def _end_value(self, consumed_terminator: bool) -> None:
        self.seg = self.field_idx + 1
        self.mode, self.pos = "forced", 1 if consumed_terminator else 0
        if consumed_terminator and self.pos == len(self.g.segments[self.seg]):
            self._after_segment()


# --------------------------------------------------------------------------- readings from step probabilities

@dataclass
class StepDist:
    """The chosen token at one step and the top allowed tokens with their probabilities."""

    chosen: int
    top: list[tuple[int, float]]   # (token id, probability), most probable first

    def p(self, tok: int) -> float:
        return next((p for t, p in self.top if t == tok), 0.0)


def field_reading(steps: list[StepDist], g: OutputGrammar, unclear: bool,
                  max_alternatives: int = 6) -> FieldReading:
    """Value, confidence and alternatives of one field from its value steps (CLAUDE.md 4.4).

    Confidence is the product of the probabilities of the tokens that decide the value:
    the digits plus the step that ends the number, or the `null` token.
    Alternatives: the top-1 reading, single-digit substitutions from the top tokens at
    each position, a one-digit-shorter and one-digit-longer reading, and `null`.
    """
    digits = g.digit_ids
    first = steps[0]
    if first.chosen not in digits:                                   # null
        conf = first.p(first.chosen)
        cands: dict[int | None, float] = {None: conf}
        for tok, p in first.top:
            if tok in digits:
                cands.setdefault(digits[tok], p)
    else:
        digit_steps = [s for s in steps if s.chosen in digits]
        term_step = steps[len(digit_steps)] if len(steps) > len(digit_steps) else None
        ds = [digits[s.chosen] for s in digit_steps]
        probs = [s.p(s.chosen) for s in digit_steps]
        p_term = term_step.p(term_step.chosen) if term_step else 1.0
        conf = math.prod(probs) * p_term

        def num(seq: list[int]) -> int:
            return int("".join(map(str, seq)))

        cands = {num(ds): conf}
        for i, s in enumerate(digit_steps):                          # substitutions
            for tok, p in s.top:
                if tok in digits and tok != s.chosen:
                    alt = ds[:i] + [digits[tok]] + ds[i + 1:]
                    if not (i == 0 and digits[tok] == 0 and len(ds) > 1):
                        score = conf / max(probs[i], 1e-12) * p
                        cands[num(alt)] = max(cands.get(num(alt), 0.0), score)
        for i in range(1, len(ds)):                                   # shorter
            term = next((p for tok, p in digit_steps[i].top if tok not in digits), 0.0)
            if term > 0:
                cands[num(ds[:i])] = max(cands.get(num(ds[:i]), 0.0), math.prod(probs[:i]) * term)
        if term_step is not None and len(ds) < MAX_DIGITS and not (ds == [0]):   # longer
            for tok, p in term_step.top:
                if tok in digits:
                    longer = num(ds + [digits[tok]])
                    cands[longer] = max(cands.get(longer, 0.0), math.prod(probs) * p)
                    break
        null_p = first.p(g.null_ids[0])
        if null_p > 0:
            cands[None] = null_p
    top1 = None if first.chosen not in digits else int("".join(
        str(digits[s.chosen]) for s in steps if s.chosen in digits))
    ranked = sorted(((v, p) for v, p in cands.items() if v != top1), key=lambda x: -x[1])
    alternatives = ((top1, conf),) + tuple(ranked[: max_alternatives - 1])
    return FieldReading.of(top1, conf, unclear, alternatives)


def readings_from_steps(machine: GrammarMachine, dists: list[StepDist],
                        g: OutputGrammar) -> dict[str, FieldReading]:
    """Turn a finished generation into one `FieldReading` per field."""
    if not machine.done:
        raise ValueError("generation did not complete the schema")
    per_field: dict[str, list[StepDist]] = {n: [] for n in g.field_names}
    for info, dist in zip(machine.trace, dists):
        if info.role == "value" and info.field is not None:
            per_field[info.field].append(dist)
    unclear = set(machine.unclear)
    return {n: field_reading(per_field[n], g, n in unclear) for n in g.field_names}
