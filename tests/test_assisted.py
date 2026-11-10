import pytest

from naijawatchtower.config import field_names
from naijawatchtower.eval.assisted import (Suggestion, is_confident, provenance, resolve_bottom, resolve_items,
                                           resolve_party)
from naijawatchtower.eval.labelling import LabelStore, parse_party_rows, run_labelling
from naijawatchtower.ocr.types import FieldReading, SheetReading


def fr(value, conf=0.99, rival=None, unclear=False):
    alts = ((value, conf),) + (((rival[0], rival[1]),) if rival else ())
    return FieldReading.of(value, conf, unclear, alts)


def test_confidence_gate():
    assert is_confident(fr(74))
    assert not is_confident(fr(74, 0.90))                     # below 0.95
    assert not is_confident(fr(74, 0.97, rival=(14, 0.2)))    # close competitor
    assert is_confident(fr(74, 0.97, rival=(14, 0.05)))       # competitor far behind
    assert not is_confident(fr(74, unclear=True))


def make_reading(sheet, uncertain=()):
    fields = {}
    for n in field_names():
        fields[n] = fr(sheet[n], 0.6, rival=(1, 0.3)) if n in uncertain else fr(sheet[n])
    return SheetReading(fields=fields)


@pytest.fixture
def zero_sheet(sheet):
    s = dict(sheet)
    for n in field_names():
        if n.startswith("party_") and s[n] is None:
            s[n] = 0                                           # zeros written in every other row
    return s


def test_suggestion_lines(zero_sheet):
    sug = Suggestion.from_reading(make_reading(zero_sheet, {"total_valid_votes", "party_07"}))
    assert sug.items_tokens() == ["600", "412", "700", "270", "18", "12", "_", "430"]
    assert sug.zero_default() and sug.party_line() == "*0 2=210 7=_ 14=40"
    assert sug.n_missing() == 2 and sug.bottom_token() == "400"


def test_resolve_items(zero_sheet):
    sug = Suggestion.from_reading(make_reading(zero_sheet, {"total_valid_votes"}))
    assert resolve_items(sug, "400") == "600 412 700 270 18 12 400 430"            # fill the gap
    assert resolve_items(sug, "400 2=413") == "600 413 700 270 18 12 400 430"      # fill + correct
    assert resolve_items(sug, "1 2 3 4 5 6 7 8") == "1 2 3 4 5 6 7 8"              # whole line
    assert resolve_items(sug, "7=5+74") == "600 412 700 270 18 12 5+74 430"
    assert resolve_items(sug, "5+74=79") == "600 412 700 270 18 12 5+74=79 430"   # written equation fills the gap
    assert resolve_items(sug, "1 2 3 4 5 6 7 5+74=79").endswith("5+74=79")       # ... or sits in a full line
    with pytest.raises(ValueError, match="still to read"):
        resolve_items(sug, "")
    with pytest.raises(ValueError):
        resolve_items(sug, "400 401")
    full = Suggestion.from_reading(make_reading(zero_sheet))
    assert resolve_items(full, "") == "600 412 700 270 18 12 400 430"               # Enter accepts


def test_resolve_party_and_bottom(zero_sheet):
    sug = Suggestion.from_reading(make_reading(zero_sheet, {"party_07", "total_valid_votes_bottom"}))
    line = resolve_party(sug, "150")
    assert parse_party_rows(line)["party_07"] == 150 and parse_party_rows(line)["party_01"] == 0
    line = resolve_party(sug, "150 3=9")
    assert parse_party_rows(line)["party_03"] == 9
    assert parse_party_rows(resolve_party(sug, "!6=120"))["party_06"] == 120        # replace entirely
    assert parse_party_rows(resolve_party(sug, "!6=120"))["party_01"] is None
    with pytest.raises(ValueError, match="row 7"):
        resolve_party(sug, "")
    with pytest.raises(ValueError):
        resolve_bottom(sug, "")
    assert resolve_bottom(sug, "400") == "400"


def test_assisted_run_records_provenance(pattern_image, zero_sheet):
    data_dir = pattern_image.parent.parent
    store = LabelStore.load(data_dir)
    sug = Suggestion.from_reading(make_reading(zero_sheet, {"total_valid_votes", "party_07"}))
    answers = ["Lagos", "400", "150 2=211", "", "y"]           # fill item 7, fill row 7, correct row 2
    it = iter(answers)
    assert run_labelling(store, lambda q: next(it), lambda s: None, suggest=lambda p: sug) == 1
    again = LabelStore.load(data_dir)
    labels, prov = again.labels["R001"], again.provenance["R001"]
    assert labels["total_valid_votes"] == 400 and labels["party_07"] == 150 and labels["party_02"] == 211
    assert prov["total_valid_votes"] == "typed" and prov["party_07"] == "typed"
    assert prov["party_02"] == "model_corrected" and prov["registered_voters"] == "model_accepted"
    assert again.sheets["R001"]["label_mode"] == "assisted"
    assert provenance(None, labels)["party_02"] == "typed"
