from naijawatchtower.validate.rules import check_rules, failed_rules, hard_rules_pass


def ids(results):
    return {r.rule_id for r in results}


def test_consistent_sheet_passes_all(sheet):
    assert all(r.passed for r in check_rules(sheet))
    assert {r.rule_id for r in check_rules(sheet)} == {f"R{i}" for i in range(1, 8)}


def test_r1_used_ballots(sheet):
    sheet["total_used_ballots"] = 431
    assert "R1" in ids(failed_rules(sheet))


def test_r2_party_sum(sheet):
    sheet["party_07"] = 151  # one digit altered
    assert ids(failed_rules(sheet, "hard")) == {"R2"}


def test_r3_only_when_bottom_present(sheet):
    sheet["total_valid_votes_bottom"] = None
    assert hard_rules_pass(sheet)
    sheet["total_valid_votes_bottom"] = 401
    assert ids(failed_rules(sheet)) == {"R3"}


def test_r4_issued(sheet):
    sheet["ballots_unused"] = 271
    assert ids(failed_rules(sheet, "hard")) == {"R4"}


def test_r5_overaccreditation(sheet):
    sheet["registered_voters"] = 411
    assert "R5" in ids(failed_rules(sheet))


def test_r6_range(sheet):
    sheet["party_02"] = 10000
    assert "R6" in ids(failed_rules(sheet))


def test_r7_is_soft(sheet):
    sheet["accredited_voters"] = 420
    failed = failed_rules(sheet)
    assert ids(failed) == {"R7"} and failed[0].severity == "soft"
    assert hard_rules_pass(sheet)


def test_blank_counts_as_zero(sheet):
    sheet["ballots_spoiled"] = None
    sheet["total_used_ballots"] = 412
    sheet["ballots_unused"] = 288
    assert hard_rules_pass(sheet)
