import numpy as np

from naijawatchtower.config import field_names
from naijawatchtower.sim.election import (PARTY_NAMES, consistent_values, inject_defect,
                                          simulate_election)
from naijawatchtower.validate.rules import check_rules, failed_rules, hard_rules_pass


def test_consistent_values_satisfy_all_rules_1000():
    rng = np.random.default_rng(123)
    for _ in range(1000):
        values = consistent_values(rng)
        failed = [r.rule_id for r in check_rules(values) if not r.passed]
        assert failed == [], (failed, values)


def test_defects_break_the_expected_rules():
    rng = np.random.default_rng(5)
    for _ in range(200):
        base = consistent_values(rng)
        tampered, _ = inject_defect(base, "tampered_digit", rng)
        assert "R2" in {r.rule_id for r in failed_rules(tampered, "hard")}
        mismatch, _ = inject_defect(base, "sum_mismatch_entry", rng)
        assert not hard_rules_pass(mismatch)
        over, _ = inject_defect(base, "overvote", rng)
        assert {r.rule_id for r in failed_rules(over, "hard")} & {"R4", "R5"}


def test_election_shape():
    units, records, empty = simulate_election(seed=7, exclude_states={24, 34})
    assert all(u.numeric_code.startswith("9") and len(u.numeric_code) == 10 for u in units)
    assert len({u.numeric_code for u in units}) == len(units)
    assert {u.state_number for u in units}.isdisjoint({24, 34})       # real-sheet states excluded
    assert len({u.state_number for u in units}) == 35
    assert 90 <= len(units) <= 120
    with_records = {r.unit_code for r in records}
    assert 0.7 <= len(with_records) / len(units) <= 0.85
    assert len(empty) >= 2
    for state in empty:
        assert not any(u.state_number == state and u.code in with_records for u in units)


def test_records_consistent_unless_defective():
    _, records, _ = simulate_election(seed=11)
    clean = {"none", "illegible", "low_confidence", "close_alternative"}
    for rec in records:
        assert set(rec.reading.fields) == set(field_names())
        if rec.defect in clean:
            assert hard_rules_pass(rec.truth), rec.defect
        if rec.defect == "none":
            assert rec.reading.values() == rec.truth
            assert min(f.confidence for f in rec.reading.fields.values()) >= 0.95


def test_reading_alternatives_are_valid():
    _, records, _ = simulate_election(seed=3)
    for rec in records:
        for name, f in rec.reading.fields.items():
            assert f.alternatives[0] == (f.value, f.confidence)
            assert sum(p for _, p in f.alternatives) <= 1.0 + 1e-9
            assert any(v == rec.truth[name] for v, _ in f.alternatives), (rec.defect, name)


def test_fictional_party_names():
    assert PARTY_NAMES[0] == "Party A" and PARTY_NAMES[-1] == "Party R" and len(PARTY_NAMES) == 18
