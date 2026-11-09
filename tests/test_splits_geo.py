import json

from naijawatchtower.config import ROOT, states
from naijawatchtower.eval.splits import make_splits


def test_splits_disjoint_deterministic_stratified():
    sheet_states = {f"R{i:03d}": ("024" if i <= 18 else "034" if i <= 26 else "019") for i in range(1, 31)}
    a = make_splits(sheet_states, seed=0)
    assert a == make_splits(sheet_states, seed=0)
    assert set(a["dev"]).isdisjoint(a["test"])
    assert set(a["dev"]) | set(a["test"]) == set(sheet_states)
    assert len(a["dev"]) == 8                                   # ceil(0.25 * 30)
    dev_states = [sheet_states[s] for s in a["dev"]]
    assert dev_states.count("024") >= 4 and dev_states.count("034") >= 2
    assert make_splits(sheet_states, seed=1)["dev"] != a["dev"]


def test_splits_small_sets():
    assert make_splits({}) == {"dev": [], "test": []}
    assert len(make_splits({"R001": "024", "R002": "024"})["dev"]) == 1
    assert len(make_splits({f"R{i:03d}": "024" for i in range(20)})["dev"]) == 5


def test_boundaries_cover_all_37_regions():
    geo = json.loads((ROOT / "data/geo/nigeria_admin1.geojson").read_text())
    assert len(geo["features"]) == 37
    assert {f["properties"]["number"] for f in geo["features"]} == {s["number"] for s in states()}
    assert "CC BY 4.0" in geo["attribution"]
    assert (ROOT / "data/geo/nigeria_admin1.geojson").stat().st_size < 150_000
