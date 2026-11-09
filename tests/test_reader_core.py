"""Reader-independent M1 pieces: grammar, confidence/alternatives, decoding, schema, cache."""

import json
import math

import pytest

from naijawatchtower.config import field_names
from naijawatchtower.ocr.cache_reader import CacheMiss, CacheReader, store_reading
from naijawatchtower.ocr.grammar import OutputGrammar, StepDist, field_reading, readings_from_steps
from naijawatchtower.ocr.schema import SchemaError, parse_output, to_output_json
from naijawatchtower.ocr.types import FieldReading, SheetReading
from naijawatchtower.validate.decoding import (AMBIGUOUS, NO_CONSISTENT_READING, NOT_NEEDED,
                                               REPAIRED_SUGGESTION, constraint_decode)

EOS = 0


def char_grammar():
    return OutputGrammar(encode=lambda s: [ord(c) for c in s], field_names=field_names(), eos_id=EOS)


def drive(g, text):
    """Feed `text` char by char; every char must be allowed. Returns the machine."""
    m = g.machine()
    for ch in text:
        assert ord(ch) in m.allowed(), (ch, m.mode, m.field_idx)
        m.advance(ord(ch))
    assert m.allowed() == [EOS]
    return m


def test_grammar_accepts_schema_and_parses(sheet):
    g = char_grammar()
    text = to_output_json(sheet, ["party_07", "ballots_spoiled"])
    m = drive(g, text)
    assert m.done and m.unclear == ["party_07", "ballots_spoiled"]
    assert parse_output(text) == (sheet, ["party_07", "ballots_spoiled"])


def test_grammar_rejects_free_text_and_bad_numbers(sheet):
    g = char_grammar()
    m = g.machine()
    for ch in '{"fields":{"registered_voters":':
        m.advance(ord(ch))
    assert ord("I") not in m.allowed() and ord("-") not in m.allowed()   # no text, no minus
    for ch in "1234":
        m.advance(ord(ch))
    assert m.allowed() == [ord(",")]                                     # max 4 digits
    m2 = g.machine()
    for ch in '{"fields":{"registered_voters":0':
        m2.advance(ord(ch))
    assert m2.allowed() == [ord(",")]                                    # no leading zero
    with pytest.raises(ValueError):
        m2.advance(ord("7"))


def test_readings_from_steps_round_trip(sheet):
    g = char_grammar()
    text = to_output_json(sheet, ["party_14"])
    m = drive(g, text)
    dists = [StepDist(ord(ch), [(ord(ch), 0.97)]) for ch in text]
    readings = readings_from_steps(m, dists, g)
    assert {n: r.value for n, r in readings.items()} == sheet
    assert readings["party_14"].unclear and not readings["party_02"].unclear
    assert math.isclose(readings["party_02"].confidence, 0.97 ** 4)      # 3 digits + end
    assert math.isclose(readings["party_01"].confidence, 0.97)            # null


def test_field_reading_alternatives():
    g = char_grammar()
    d = lambda c: ord(c)  # noqa: E731
    term = ord(",")
    steps = [StepDist(d("1"), [(d("1"), 0.9), (d("7"), 0.1)]),
             StepDist(d("2"), [(d("2"), 0.6), (d("8"), 0.3), (term, 0.1)]),
             StepDist(term, [(term, 0.95), (d("0"), 0.05)])]
    r = field_reading(steps, g, unclear=False)
    assert r.value == 12 and math.isclose(r.confidence, 0.9 * 0.6 * 0.95)
    alts = dict(r.alternatives)
    assert [v for v, _ in r.alternatives] == [12, 18, 1, 72, 120]
    assert math.isclose(alts[18], 0.9 * 0.3 * 0.95) and math.isclose(alts[1], 0.9 * 0.1)


def reading_from(values, overrides=None):
    overrides = overrides or {}
    fields = {}
    for n in field_names():
        if n in overrides:
            fields[n] = overrides[n]
        else:
            fields[n] = FieldReading.of(values[n], 0.98, alternatives=((values[n], 0.98),))
    return fields


def test_decoding_finds_known_unique_repair(sheet):
    # party_07 is really 150 but was read as 156 with a 0.3 alternative of 150.
    misread = FieldReading.of(156, 0.6, alternatives=((156, 0.6), (150, 0.3), (166, 0.05)))
    result = constraint_decode(reading_from(sheet, {"party_07": misread}))
    assert result.outcome == REPAIRED_SUGGESTION
    assert result.best["party_07"] == 150 and result.changed == ["party_07"]


def test_decoding_not_needed_and_no_consistent(sheet):
    assert constraint_decode(reading_from(sheet)).outcome == NOT_NEEDED
    # a tampered value with no alternative that fixes the sum
    tampered = FieldReading.of(156, 0.99, alternatives=((156, 0.99), (166, 0.005)))
    assert constraint_decode(reading_from(sheet, {"party_07": tampered})).outcome == NO_CONSISTENT_READING


def test_decoding_ambiguous(sheet):
    # Either party_02 or party_07 may be misread by 6: two equally good repairs.
    a = FieldReading.of(216, 0.5, alternatives=((216, 0.5), (210, 0.4)))
    b = FieldReading.of(150, 0.5, alternatives=((150, 0.5), (144, 0.4)))
    result = constraint_decode(reading_from(sheet, {"party_02": a, "party_07": b}))
    assert result.outcome == AMBIGUOUS and result.second is not None


@pytest.mark.parametrize("bad", [
    "not json",
    '{"fields": {}, "unclear": []}',
    "EXTRA",
    "RANGE",
    "STRING",
    "UNKNOWN_UNCLEAR",
])
def test_schema_rejects(bad, sheet):
    good = json.loads(to_output_json(sheet))
    if bad == "EXTRA":
        good["fields"]["presiding_officer"] = 1
        bad = json.dumps(good)
    elif bad == "RANGE":
        good["fields"]["party_02"] = 10000
        bad = json.dumps(good)
    elif bad == "STRING":
        good["fields"]["party_02"] = "210"
        bad = json.dumps(good)
    elif bad == "UNKNOWN_UNCLEAR":
        good["unclear"] = ["name_of_agent"]
        bad = json.dumps(good)
    with pytest.raises(SchemaError):
        parse_output(bad)


def test_cache_replay_matches_stored(pattern_image, tmp_path, sheet):
    fields = reading_from(sheet, {"party_07": FieldReading.of(156, 0.6, True, ((156, 0.6), (150, 0.3)))})
    live = SheetReading(fields=fields, model_revision="m@abc", prompt_version="read_sheet_v1")
    store_reading(pattern_image, live, tmp_path / "cache")
    replay = CacheReader([tmp_path / "cache"]).read(pattern_image)
    assert replay.fields == live.fields and replay.model_revision == "m@abc"
    with pytest.raises(CacheMiss):
        CacheReader([tmp_path / "empty"]).read(pattern_image)
