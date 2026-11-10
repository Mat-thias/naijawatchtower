"""M2 acceptance tests (CLAUDE.md 11): backend with FakeReader, no GPU."""

from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from naijawatchtower.api.app import Settings, create_app
from naijawatchtower.api.db import (AuditLog, CaseRef, Correction, Discrepancy, ResultSheet, SheetJob,
                                    Verification, make_sessionmaker)
from naijawatchtower.api.security import make_token
from naijawatchtower.api.seed import add_real_unit, add_sim_record, add_sim_unit, add_user
from naijawatchtower.config import field_names
from naijawatchtower.ocr.types import FieldReading, SheetReading
from naijawatchtower.sim.election import make_units
from tests.conftest import consistent_values

PHONES = {"ingest": "08030000001", "reviewer": "08030000002", "citizen": "08030000003",
          "citizen2": "08030000004", "citizen3": "08030000005", "observer": "08030000006",
          "other": "08030000007"}


def reading_of(values, conf=0.99, overrides=None):
    fields = {n: FieldReading.of(values[n], conf, alternatives=((values[n], conf),)) for n in field_names()}
    fields.update(overrides or {})
    return SheetReading(fields=fields, model_revision="fake", prompt_version="fake")


class RecordingReader:
    """Reads every image as a consistent sheet and records the order of images read."""

    def __init__(self):
        self.order: list[str] = []
        self.next: list[SheetReading] = []

    def read(self, path):
        self.order.append(Path(path).name)
        return self.next.pop(0) if self.next else reading_of(consistent_values())


@pytest.fixture
def env(tmp_path):
    db_path = tmp_path / "app.db"
    sessions = make_sessionmaker(f"sqlite:///{db_path}")
    reader = RecordingReader()
    app = create_app(Settings(db_url=f"sqlite:///{db_path}", upload_dir=tmp_path / "uploads"),
                     reader_factory=lambda: reader, sessions=sessions)
    app.state.ip_bucket.capacity = 10_000
    rng = np.random.default_rng(0)
    with sessions() as db:
        units = {}
        for u in make_units(rng, exclude_states={34})[:6]:          # Abia and Adamawa, 3 units each
            units[u.code] = add_sim_unit(db, u)
        real = add_real_unit(db, 34, 1)                              # one redacted real unit in Taraba
        units[real.code] = real
        codes = list(units)
        users = {
            "ingest": add_user(db, PHONES["ingest"], "ingest"),
            "reviewer": add_user(db, PHONES["reviewer"], "reviewer"),
            "citizen": add_user(db, PHONES["citizen"], "citizen", units[codes[0]], opted_in=True),
            "citizen2": add_user(db, PHONES["citizen2"], "citizen", units[codes[0]]),
            "citizen3": add_user(db, PHONES["citizen3"], "citizen", units[codes[0]]),
            "observer": add_user(db, PHONES["observer"], "observer", units[codes[0]]),
            "other": add_user(db, PHONES["other"], "citizen", units[codes[1]]),
        }
        db.commit()
        tokens = {k: make_token(u.id) for k, u in users.items()}
        unit_ids = {c: u.id for c, u in units.items()}
    client = TestClient(app)
    return {"client": client, "app": app, "sessions": sessions, "reader": reader, "tokens": tokens,
            "codes": codes, "unit_ids": unit_ids, "db_path": db_path, "tmp": tmp_path}


def jpeg_bytes(seed=0, exif=False):
    arr = (np.random.default_rng(seed).random((60, 40, 3)) * 255).astype("uint8")
    buf = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "PhoneCam"
        Image.fromarray(arr).save(buf, format="JPEG", exif=e)
    else:
        Image.fromarray(arr).save(buf, format="JPEG")
    return buf.getvalue()


def auth(env, who):
    return {"Authorization": f"Bearer {env['tokens'][who]}"}


def upload(env, code, seed=0, who="ingest", data=None):
    return env["client"].post("/sheets", data={"pu_code": code}, headers=auth(env, who),
                              files={"file": ("sheet.jpg", data or jpeg_bytes(seed), "image/jpeg")})


def ready_sheet(env, code_index=0, seed=0):
    r = upload(env, env["codes"][code_index], seed)
    assert r.status_code == 202, r.text
    env["app"].state.queue.drain()
    return r.json()["id"]


# ------------------------------------------------------------------ ingest and queue

def test_upload_returns_processing_and_queue_runs_in_order(env):
    ids = []
    for i in range(3):
        r = upload(env, env["codes"][i], seed=i)
        assert r.status_code == 202 and r.json()["status"] == "PROCESSING"
        ids.append(r.json())
    env["app"].state.queue.drain()
    assert env["reader"].order == [f"{x['image_sha256']}.jpg" for x in ids]
    with env["sessions"]() as db:
        jobs = db.execute(select(SheetJob).order_by(SheetJob.id)).scalars().all()
        assert [j.state for j in jobs] == ["done"] * 3
        assert all(j.started_at and j.finished_at and j.seconds is not None for j in jobs)
        assert jobs[0].finished_at <= jobs[1].started_at
        assert db.get(ResultSheet, ids[0]["id"]).status == "DIGITISED_UNVERIFIED"


def test_citizen_cannot_ingest_and_bad_files_rejected(env):
    assert upload(env, env["codes"][0], who="citizen").status_code == 403
    assert env["client"].post("/sheets", data={"pu_code": env["codes"][0]}).status_code == 401
    bad = env["client"].post("/sheets", data={"pu_code": env["codes"][0]}, headers=auth(env, "ingest"),
                             files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert bad.status_code == 415
    big = env["client"].post("/sheets", data={"pu_code": env["codes"][0]}, headers=auth(env, "ingest"),
                             files={"file": ("x.jpg", b"\xff" * (8 * 1024 * 1024 + 10), "image/jpeg")})
    assert big.status_code == 413


def test_exif_is_stripped_and_hash_recorded(env):
    r = upload(env, env["codes"][0], data=jpeg_bytes(exif=True))
    with env["sessions"]() as db:
        sheet = db.get(ResultSheet, r.json()["id"])
        with Image.open(sheet.image_path) as img:
            assert not img.getexif()
        import hashlib
        assert hashlib.sha256(Path(sheet.image_path).read_bytes()).hexdigest() == sheet.image_sha256


# ------------------------------------------------------------------ verification

def test_wrong_unit_and_one_verification_per_user(env):
    sid = ready_sheet(env)
    c = env["client"]
    assert c.post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, "other")
                  ).status_code == 403
    first = c.post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, "citizen"))
    assert first.status_code == 201
    again = c.post(f"/sheets/{sid}/verifications", json={"verdict": "dispute"}, headers=auth(env, "citizen"))
    assert again.status_code == 409 and first.json()["ref"] in again.json()["detail"]
    assert c.post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, "ingest")
                  ).status_code == 403


def test_status_rules_verified_and_disputed(env):
    sid = ready_sheet(env)
    c = env["client"]
    for who in ("citizen", "citizen2"):
        c.post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, who))
    assert c.get(f"/sheets/{sid}").json()["status"] == "DIGITISED_UNVERIFIED"   # weight 2 < 3
    r = c.post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, "citizen3"))
    assert r.json()["status"] == "VERIFIED"
    detail = c.get(f"/sheets/{sid}").json()
    assert detail["verification_counts"]["confirm"] == {"count": 3, "weight": 3}
    assert "user" not in str(detail["verification_counts"])                   # counts only
    r = c.post(f"/sheets/{sid}/verifications", json={"verdict": "dispute", "comment": "row 2 differs",
                                                     "field_corrections": [{"field": "party_02", "value": 211}]},
               headers=auth(env, "observer"))
    assert r.json()["status"] == "DISPUTED"
    with env["sessions"]() as db:
        assert db.execute(select(Discrepancy).where(Discrepancy.type == "VERIFIER_DISPUTE")).scalar_one()
        assert db.execute(select(Correction)).scalar_one().proposed_value == 211


def test_needs_review_sheet_cannot_be_verified_by_confirmations(env):
    values = consistent_values()
    env["reader"].next.append(reading_of(values, conf=0.5))           # low confidence -> NEEDS_REVIEW
    sid = ready_sheet(env)
    c = env["client"]
    assert c.get(f"/sheets/{sid}").json()["status"] == "NEEDS_REVIEW"
    for who in ("citizen", "citizen2", "citizen3"):
        c.post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, who))
    assert c.get(f"/sheets/{sid}").json()["status"] == "NEEDS_REVIEW"


# ------------------------------------------------------------------ decoding, review, cases

def test_repair_is_suggested_never_applied_and_review_resolves(env):
    values = consistent_values()
    misread = FieldReading.of(156, 0.6, alternatives=((156, 0.6), (150, 0.3)))
    env["reader"].next.append(reading_of(values, overrides={"party_07": misread}))
    sid = ready_sheet(env)
    c = env["client"]
    d = c.get(f"/sheets/{sid}").json()
    p7 = next(f for f in d["fields"] if f["name"] == "party_07")
    assert d["status"] == "NEEDS_REVIEW" and p7["value"] == 156 and p7["suggestion"] == 150
    assert d["detail"]["decoding"]["outcome"] == "REPAIRED_SUGGESTION"
    q = c.get("/review/queue", headers=auth(env, "reviewer")).json()["queue"]
    assert q[0]["sheet_id"] == sid
    assert c.get("/review/queue", headers=auth(env, "citizen")).status_code == 403
    bad = c.post(f"/sheets/{sid}/review", json={"decision": "modify", "final_values": {"party_07": 150},
                                                 "reason_code": "because"}, headers=auth(env, "reviewer"))
    assert bad.status_code == 422
    ok = c.post(f"/sheets/{sid}/review", json={"decision": "modify", "final_values": {"party_07": 150},
                                                "reason_code": "reading_corrected"}, headers=auth(env, "reviewer"))
    assert ok.json()["status"] == "REVIEWED"
    d = c.get(f"/sheets/{sid}").json()
    p7 = next(f for f in d["fields"] if f["name"] == "party_07")
    assert p7["value"] == 150 and p7["model_value"] == 156 and p7["source"] == "human_review"
    assert all(r["passed"] for r in d["rules"])                       # live check on the reviewed values
    with env["sessions"]() as db:
        assert db.execute(select(Discrepancy).where(Discrepancy.type == "MODEL_VS_HUMAN_MISMATCH")).scalar_one()


def test_inconsistent_sheet_creates_discrepancy_and_case_tracking(env):
    values = consistent_values()
    tampered = FieldReading.of(156, 0.99, alternatives=((156, 0.99),))
    env["reader"].next.append(reading_of(values, overrides={"party_07": tampered}))
    sid = ready_sheet(env)
    c = env["client"]
    with env["sessions"]() as db:
        d = db.execute(select(Discrepancy).where(Discrepancy.sheet_id == sid)).scalar_one()
        assert d.type == "ARITHMETIC_INCONSISTENT" and d.note == "These figures do not add up as the form requires."
    r = c.post(f"/sheets/{sid}/verifications", json={"verdict": "dispute"}, headers=auth(env, "citizen"))
    ref = r.json()["ref"]
    case = c.get(f"/case/{ref}").json()
    assert case["status"] == "received" and set(case) == {"ref", "status", "updated_at", "reason"}
    assert c.get("/case/00000").status_code == 404
    c.post(f"/sheets/{sid}/review", json={"decision": "accept", "reason_code": "confirmed_as_written"},
           headers=auth(env, "reviewer"))
    case = c.get(f"/case/{ref}").json()
    assert case["status"] == "resolved_no_change" and case["reason"] == "Sheet confirmed as written."
    rows = c.get("/discrepancies").json()["discrepancies"]
    assert any(r["type"] == "ARITHMETIC_INCONSISTENT" and r["status"] == "open" for r in rows)  # still inconsistent
    csv_text = c.get("/export/discrepancies.csv").text
    assert csv_text.startswith("# DEMO DATA") and "UNOFFICIAL" in csv_text and "phone" not in csv_text


# ------------------------------------------------------------------ audit

def test_audit_chain_detects_tampering(env):
    ready_sheet(env)
    c = env["client"]
    assert c.get("/audit/verify").json()["ok"] is True
    con = sqlite3.connect(env["db_path"])
    target = con.execute("SELECT id FROM audit_log ORDER BY id LIMIT 1 OFFSET 1").fetchone()[0]
    con.execute("UPDATE audit_log SET action='edited' WHERE id=?", (target,))
    con.commit()
    con.close()
    result = c.get("/audit/verify").json()
    assert result == {"ok": False, "first_broken_id": target, "entries": result["entries"]}


# ------------------------------------------------------------------ aggregates

def test_aggregate_coverage_matches_hand_computed_fixture(env):
    c = env["client"]
    s1 = ready_sheet(env, 0, seed=1)                                   # Abia unit 1: digitised
    ready_sheet(env, 1, seed=2)                                        # Abia unit 2: digitised
    for who in ("citizen", "citizen2", "citizen3"):                    # unit 1 -> VERIFIED
        c.post(f"/sheets/{s1}/verifications", json={"verdict": "confirm"}, headers=auth(env, who))
    agg = c.get("/aggregate", params={"scope": "state", "code": "001"}).json()
    sim = agg["coverage"]["simulated"]
    assert (sim["expected"], sim["digitised"], sim["verified"], sim["no_sheet"]) == (3, 2, 1, 1)
    assert agg["coverage"]["real_redacted"]["expected"] == 0
    v = consistent_values()
    assert agg["party_totals_scope"] == "simulated_records_only"
    assert agg["verified_only"]["sheets"] == 1 and agg["all_digitised"]["sheets"] == 2
    assert agg["all_digitised"]["totals"]["Party B"] == 2 * v["party_02"]
    assert agg["verified_only"]["totals"]["Party G"] == v["party_07"]
    assert list(agg["all_digitised"]["totals"]) == sorted(agg["all_digitised"]["totals"])  # alphabetical
    states = {s["code"]: s for s in c.get("/geo/states", params={"layer": "coverage"}).json()["states"]}
    assert len(states) == 37
    assert states["001"]["value"] == pytest.approx(2 / 3) and states["002"]["value"] == 0.0
    assert states["003"]["value"] is None                              # no units -> no data
    verified = {s["code"]: s for s in c.get("/geo/states", params={"layer": "verified"}).json()["states"]}
    assert verified["001"]["value"] == pytest.approx(1 / 2)


def test_real_sheets_never_enter_party_totals(env):
    real_code = env["codes"][-1]
    r = upload(env, real_code, seed=9)
    env["app"].state.queue.drain()
    with env["sessions"]() as db:
        assert db.get(ResultSheet, r.json()["id"]).source == "real_redacted"
    agg = env["client"].get("/aggregate").json()
    assert agg["coverage"]["real_redacted"]["digitised"] == 1
    assert agg["party_totals_scope"] == "simulated_records_only"
    assert all(v == 0 for v in agg["all_digitised"]["totals"].values())
    panel = env["client"].get("/geo/state/34").json()
    assert panel["has_real_sheets"] and panel["all_digitised"]["sheets"] == 0
    detail = env["client"].get(f"/sheets/{r.json()['id']}").json()
    assert [f["label"] for f in detail["fields"] if f["name"] == "party_03"] == ["Row 3"]
    assert "Party" not in str([f["label"] for f in detail["fields"]])


def test_simulated_record_pipeline(env):
    with env["sessions"]() as db:
        from naijawatchtower.api.db import PollingUnit
        unit = db.execute(select(PollingUnit).where(PollingUnit.code == env["codes"][2])).scalar_one()
        sheet = add_sim_record(db, unit, reading_of(consistent_values()))
        db.commit()
        sid = sheet.id
    d = env["client"].get(f"/sheets/{sid}").json()
    assert d["image_url"] is None and d["source"] == "simulated" and d["status"] == "DIGITISED_UNVERIFIED"
    assert next(f for f in d["fields"] if f["name"] == "party_02")["label"] == "Party B"


# ------------------------------------------------------------------ privacy and limits

def test_no_raw_phone_numbers_in_database(env):
    sid = ready_sheet(env)
    env["client"].post(f"/sheets/{sid}/verifications", json={"verdict": "confirm"}, headers=auth(env, "citizen"))
    env["client"].post("/ussd", data={"phoneNumber": "+2348030000003", "text": "1"},
                       headers={"X-USSD-Secret": "dev-only-ussd-secret"})
    blob = Path(env["db_path"]).read_bytes()
    for phone in PHONES.values():
        for form in (phone, "234" + phone[1:], phone[1:]):
            assert form.encode() not in blob, form


def test_rate_limit_per_ip(env):
    env["app"].state.ip_bucket.capacity = 3
    env["app"].state.ip_bucket.state.clear()
    codes = [env["client"].get("/aggregate").status_code for _ in range(5)]
    assert codes.count(429) >= 1


def test_otp_flow_simulated(env, caplog):
    c = env["client"]
    caplog.set_level("WARNING")
    assert c.post("/auth/otp/request", json={"phone": "08039999999"}).json()["sent"]
    code = re.search(r"DEMO OTP for user \w+\.\.\.: (\d{6})", caplog.text).group(1)
    assert "08039999999" not in caplog.text and "2348039999999" not in caplog.text
    r = c.post("/auth/otp/verify", json={"phone": "08039999999", "otp": code, "pu_code": env["codes"][0],
                                         "invite_code": "DEMO-OBSERVER"})
    assert r.status_code == 200 and r.json()["role"] == "observer"
    assert c.post("/auth/otp/verify", json={"phone": "08039999999", "otp": code}).status_code == 401
