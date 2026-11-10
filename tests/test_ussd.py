"""USSD tests (CLAUDE.md 7): screen lengths, flows, parity with the web path, secret, privacy."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from naijawatchtower.api.db import AuditLog, CaseRef, Correction, ResultSheet, User, Verification
from naijawatchtower.api.security import phone_hash
from naijawatchtower.api.services import review_queue
from naijawatchtower.api.ussd import handle_ussd, walk
from tests.test_api import PHONES, auth, env, ready_sheet  # noqa: F401  (fixture re-export)

BANNED = ("winner", "leading", "projected", "fraud", "rigging", "vote now", "go and vote")
RESULT_SCREENS = {"VerifyPage", "VerifyQuestion"}
NEW_PHONE = "08051112222"


def isolated_sessions(env):
    """Factory of sessions on a throwaway copy of the test database (one copy per replay)."""
    import contextlib
    import shutil
    import tempfile

    from naijawatchtower.api.db import make_sessionmaker

    @contextlib.contextmanager
    def fresh():
        with tempfile.TemporaryDirectory(dir=env["tmp"]) as d:
            copy = f"{d}/walk.db"
            shutil.copyfile(env["db_path"], copy)
            maker = make_sessionmaker(f"sqlite:///{copy}")
            with maker() as db:
                yield db
            maker.kw["bind"].dispose()
    return fresh


def session(env, phone, text, lang="en"):
    with env["sessions"]() as db:
        return handle_ussd(text, phone, lang, db)


def drive_to_question(env, phone):
    """Inputs from the main menu to the 'does this match' question; returns (text, n_inputs)."""
    text = "2"
    for _ in range(10):
        kind, msg = session(env, phone, text)
        if "Does this match" in msg:
            return text, len(text.split("*"))
        text += "*1"
    raise AssertionError("never reached the question")


def set_language(env, phone, lang):
    with env["sessions"]() as db:
        user = db.execute(select(User).where(User.phone_hash == phone_hash(phone))).scalar_one()
        user.language = lang
        db.commit()


@pytest.mark.parametrize("lang", ["en", "pcm"])
def test_every_reachable_screen_fits_160_characters(env, lang):
    ready_sheet(env)                                                  # citizen's unit has a digitised sheet
    set_language(env, PHONES["citizen"], lang)
    set_language(env, PHONES["other"], lang)                          # registered, no sheet yet
    screens = []
    for phone in (PHONES["citizen"], PHONES["other"], NEW_PHONE):
        screens += walk(phone, lang, isolated_sessions(env), max_depth=7)
    assert len(screens) > 60
    for s in screens:
        assert len(s["message"]) <= 160, (s["text"], len(s["message"]), s["message"])
        assert s["message"].startswith("[DEMO] ")
        assert not any(b in s["message"].lower() for b in BANNED), s["message"]
        if s["screen"] in RESULT_SCREENS or "Disputes" in s["message"] or "Complain" in s["message"]:
            assert "UNOFFICIAL" in s["message"], s["message"]
    kinds = {s["screen"] for s in screens}
    assert {"Main", "More", "Register", "RegisterOptIn", "VerifyPage", "VerifyQuestion", "LinePrompt",
            "ValuePrompt", "How", "Track", "Language", "NotRegistered", "End"} <= kinds


def test_register_then_check(env):
    code = env["codes"][2]
    with env["sessions"]() as db:
        from naijawatchtower.api.db import PollingUnit
        numeric = db.execute(select(PollingUnit.numeric_code).where(PollingUnit.code == code)).scalar_one()
    kind, msg = session(env, NEW_PHONE, "3")
    assert kind == "CON" and "10-digit" in msg
    assert "Code not found" in session(env, NEW_PHONE, "3*1234567890")[1]
    kind, msg = session(env, NEW_PHONE, f"3*{numeric}")
    assert kind == "CON" and "SMS" in msg
    kind, msg = session(env, NEW_PHONE, f"3*{numeric}*1")
    assert kind == "END" and f"Registered to unit {numeric}" in msg
    with env["sessions"]() as db:
        user = db.execute(select(User).where(User.phone_hash == phone_hash(NEW_PHONE))).scalar_one()
        assert user.pu_id == env["unit_ids"][code] and user.opted_in_notifications
    kind, msg = session(env, NEW_PHONE, "1")                          # check: 1 input
    assert kind == "END" and "not yet uploaded" in msg and "UNOFFICIAL" in msg


def test_verify_yes_then_repeat_blocked(env):
    sid = ready_sheet(env)
    text, n = drive_to_question(env, PHONES["citizen"])
    kind, msg = session(env, PHONES["citizen"], text + "*1")
    assert kind == "END" and "confirmation is recorded" in msg and n + 1 <= 8
    ref = msg.split("Ref ")[1].rstrip(".")
    assert session(env, PHONES["citizen"], "2")[1].endswith(f"Already recorded. Ref {ref}.")
    with env["sessions"]() as db:
        v = db.execute(select(Verification).where(Verification.sheet_id == sid)).scalar_one()
        assert v.channel == "ussd" and v.verdict == "confirm"


def test_verify_no_creates_same_rows_as_web(env):
    sid = ready_sheet(env)
    text, _ = drive_to_question(env, PHONES["citizen"])
    kind, msg = session(env, PHONES["citizen"], text + "*2")
    assert "line number" in msg
    kind, msg = session(env, PHONES["citizen"], text + "*2*9")            # line 9 = first filled party row
    assert "number you see" in msg
    kind, msg = session(env, PHONES["citizen"], text + "*2*9*211")
    assert kind == "END" and "reviewer will check" in msg
    ref = msg.split("Ref ")[1].rstrip(".")
    with env["sessions"]() as db:
        v = db.execute(select(Verification).where(Verification.sheet_id == sid)).scalar_one()
        corr = db.execute(select(Correction).where(Correction.sheet_id == sid)).scalar_one()
        assert v.verdict == "dispute" and v.channel == "ussd" and v.case_ref == ref
        assert corr.field_name == "party_02" and corr.proposed_value == 211 and corr.case_ref == ref
        assert db.get(CaseRef, ref).kind == "dispute"
        assert db.execute(select(AuditLog).where(AuditLog.action == "verification_dispute")).scalar_one()
        assert any(r["sheet_id"] == sid and r["pending_corrections"] for r in review_queue(db))
    # same shape as a web dispute from another user of the unit
    r = env["client"].post(f"/sheets/{sid}/verifications", headers=auth(env, "citizen2"),
                           json={"verdict": "dispute", "field_corrections": [{"field": "party_02", "value": 211}]})
    with env["sessions"]() as db:
        rows = db.execute(select(Correction).where(Correction.sheet_id == sid)).scalars().all()
        assert [(c.field_name, c.proposed_value) for c in rows] == [("party_02", 211)] * 2
        assert db.get(ResultSheet, sid).status == "DISPUTED"
    assert r.status_code == 201


def test_unregistered_and_wrong_unit_blocked(env):
    ready_sheet(env)
    assert "not registered" in session(env, NEW_PHONE, "2")[1]
    assert "not registered" in session(env, NEW_PHONE, "1")[1]
    kind, msg = session(env, PHONES["other"], "2")                    # registered elsewhere: no sheet there
    assert kind == "END" and "No result to verify yet" in msg


def test_three_invalid_inputs_end_session(env):
    assert session(env, NEW_PHONE, "8")[1].startswith("[DEMO] Invalid choice.")
    assert session(env, NEW_PHONE, "8*8")[0] == "CON"
    kind, msg = session(env, NEW_PHONE, "8*8*8")
    assert kind == "END" and "Too many invalid" in msg


def test_language_track_how_and_stop_alerts(env):
    assert "Pidgin" in session(env, PHONES["citizen"], "4*2")[1]
    kind, msg = session(env, PHONES["citizen"], "9")
    assert "Stop alert" in msg                                        # now in Pidgin
    kind, msg = session(env, PHONES["citizen"], "9*7*1*1")
    assert kind == "END" and "INEC" in msg and "UNOFFICIAL" in msg
    assert "reference" in session(env, PHONES["citizen"], "9*6*12345")[1].lower()
    session(env, PHONES["citizen"], "9*5")
    with env["sessions"]() as db:
        user = db.execute(select(User).where(User.phone_hash == phone_hash(PHONES["citizen"]))).scalar_one()
        assert user.language == "pcm" and not user.opted_in_notifications


def test_simulator_page_and_proxy(env):
    c = env["client"]
    page = c.get("/ussd-sim")
    assert page.status_code == 200 and "160" in page.text and "dev-only-ussd-secret" not in page.text
    r = c.post("/ussd-sim/send", data={"phoneNumber": "08051234567", "text": "9"})
    assert r.text.startswith("CON [DEMO] 5 Stop alerts")


def test_endpoint_secret_and_no_phone_in_logs(env, caplog):
    c = env["client"]
    form = {"sessionId": "s1", "serviceCode": "*123#", "phoneNumber": "+2348051112222", "text": ""}
    assert c.post("/ussd", data=form).status_code == 401
    assert c.post("/ussd", data=form, headers={"X-USSD-Secret": "wrong"}).status_code == 401
    caplog.set_level("DEBUG")
    r = c.post("/ussd", data=form, headers={"X-USSD-Secret": "dev-only-ussd-secret"})
    assert r.status_code == 200 and r.text.startswith("CON [DEMO] NaijaWatchTower (UNOFFICIAL)")
    r = c.post("/ussd", data={**form, "text": "3*9999999999"}, headers={"X-USSD-Secret": "dev-only-ussd-secret"})
    assert "8051112222" not in caplog.text
    with env["sessions"]() as db:
        refs = db.execute(select(AuditLog.actor_ref)).scalars().all()
        assert not any("805111" in r for r in refs)
