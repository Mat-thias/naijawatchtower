import numpy as np

from naijawatchtower.config import field_names
from naijawatchtower.eval.calibration import apply_temperature, calibrate, ece, fit_temperature
from naijawatchtower.eval.labelling import ILLEGIBLE
from naijawatchtower.eval.metrics import compare, reader_metrics, system_metrics
from naijawatchtower.ocr.types import FieldReading, SheetReading


def test_temperature_fixes_overconfidence():
    rng = np.random.default_rng(0)
    confs = list(rng.uniform(0.9, 0.999, 2000))
    correct = [bool(rng.random() < 0.75) for _ in confs]       # really right only 75% of the time
    t = fit_temperature(confs, correct)
    assert t > 1.0
    after = [apply_temperature(c, t) for c in confs]
    assert ece(after, correct) < ece(confs, correct)
    assert abs(apply_temperature(0.5, 3.0) - 0.5) < 1e-9


def reading(values, conf=0.99):
    return SheetReading(fields={n: FieldReading.of(values[n], conf, alternatives=((values[n], conf),))
                                for n in field_names()}, seconds=5.0, vram_peak_mb=7000.0)


def test_compare_and_metrics(sheet):
    labels = dict(sheet)
    labels["party_14"] = ILLEGIBLE
    good = compare(reading(sheet), labels)
    assert good.exact and good.illegible == 1 and len(good.correct) == 26
    wrong_values = {**sheet, "party_07": 156}
    bad = compare(reading(wrong_values), labels)
    assert not bad.exact and sum(bad.correct) == 25
    m = reader_metrics([good, bad, good, good])
    assert m["n_sheets"] == 4 and m["n_fields_illegible_excluded"] == 4
    assert m["sheet_exact_match"]["value"] == 0.75
    assert m["sheet_exact_match"]["ci_low"] <= 0.75 <= m["sheet_exact_match"]["ci_high"]
    s = system_metrics([good, bad], temperature=1.0, conf_mins=[0.9])
    # the wrong sheet fails R2 and is not auto-routed; the right one is
    assert s["risk_coverage"][0]["auto_ok_share"] == 0.5
    assert s["risk_coverage"][0]["wrong_but_accepted_share"] == 0.0


def test_calibrate_reading_keeps_values(sheet):
    r = calibrate(reading(sheet, 0.95), 2.0)
    assert r.values() == sheet
    assert all(f.confidence < 0.95 for f in r.fields.values())
