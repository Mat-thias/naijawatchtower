import csv

import pytest

from naijawatchtower.eval.labelling import (ILLEGIBLE, LabelStore, parse_items, parse_party_rows,
                                            parse_value, run_labelling, run_reconcile)
from naijawatchtower.ocr.types import FakeReader


def scripted(answers):
    it = iter(answers)
    return lambda prompt: next(it)


def test_parse_value():
    assert parse_value("") is None and parse_value("-") is None
    assert parse_value("?") == ILLEGIBLE
    assert parse_value("0412") == 412
    assert parse_value("5+74") == 79 and parse_value("1+2+3") == 6        # sum written in the cell
    assert parse_value("5+74=79") == 79                                     # with its written result
    assert parse_value("5+74=80") == 80                                     # the written result is the label
    for bad in ["12a", "10000", "-3", "5+", "+5", "5++7", "9000+1000"]:
        with pytest.raises(ValueError):
            parse_value(bad)


def test_parse_state_by_name_or_number():
    from naijawatchtower.eval.labelling import _parse_state
    assert _parse_state("24") == 24 and _parse_state("Lagos") == 24 and _parse_state("taraba") == 34
    assert _parse_state("cross") == 9 and _parse_state("FCT") == 37 and _parse_state("Abuja") == 37
    assert _parse_state("Niger") == 26                     # exact match wins over prefix
    for bad in ["38", "K", "Atlantis", ""]:                # 'K' is ambiguous (Kaduna, Kano, ...)
        with pytest.raises(ValueError):
            _parse_state(bad)


def test_parse_items_and_rows():
    items = parse_items("600 412 700 270 18 12 400 430")
    assert items["accredited_voters"] == 412 and items["total_used_ballots"] == 430
    with pytest.raises(ValueError):
        parse_items("600 412")
    rows = parse_party_rows("2=210 7=150 14=?")
    assert rows["party_02"] == 210 and rows["party_14"] == ILLEGIBLE and rows["party_01"] is None
    for bad in ["19=5", "x=5", "5"]:
        with pytest.raises(ValueError):
            parse_party_rows(bad)
    zeros = parse_party_rows("*0 6=120 14=-")                 # every unlisted row has 0 written
    assert zeros["party_06"] == 120 and zeros["party_14"] is None and zeros["party_01"] == 0
    assert sum(v == 0 for v in zeros.values()) == 16


def test_label_then_reconcile_end_to_end(pattern_image):
    data_dir = pattern_image.parent.parent
    store = LabelStore.load(data_dir)
    answers = ["24",                                   # state: Lagos
               "600 412 700 270 18 12 4OO 430",        # typo with letter O: rejected
               "600 412 700 270 18 12 400 18+12+400",  # item 8 written as a sum
               "2=210 7=150 14=40", "400", "y"]
    out = []
    assert run_labelling(store, scripted(answers), out.append) == 1
    assert any("Try again" in line for line in out)

    reloaded = LabelStore.load(data_dir)
    assert reloaded.sheets["R001"]["state_code"] == "024"
    assert reloaded.labels["R001"]["party_07"] == 150
    assert reloaded.labels["R001"]["total_used_ballots"] == 430
    assert reloaded.notes["R001"] == {"total_used_ballots": "written as 18+12+400"}
    assert reloaded.unlabelled() == []

    # The reader disagrees on two fields: the human keeps one and corrects the other.
    reader = FakeReader({"pattern.jpg": {**reloaded.labels["R001"], "party_07": 156, "party_14": 46}})
    changed = run_reconcile(reloaded, reader, scripted(["", "46"]), out.append)
    assert changed == 1
    final = LabelStore.load(data_dir)
    assert final.labels["R001"]["party_07"] == 150 and final.labels["R001"]["party_14"] == 46
    assert final.sheets["R001"]["reconciled_at"]
    with (data_dir / "labels.csv").open() as f:
        assert len(list(csv.DictReader(f))) == 27
