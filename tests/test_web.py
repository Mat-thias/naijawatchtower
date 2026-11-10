"""M3 acceptance tests (CLAUDE.md 9, 11): pages, banners, wording, map, drill-down, web forms."""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from naijawatchtower.api.db import PollingUnit, ResultSheet
from naijawatchtower.api.security import make_token
from naijawatchtower.api.seed import add_sim_record
from naijawatchtower.config import ROOT
from tests.conftest import consistent_values
from tests.test_api import auth, env, ready_sheet, reading_of, upload  # noqa: F401

HTML = {"Accept": "text/html"}
UNOFFICIAL = "UNOFFICIAL. Digitised from result-sheet images. Not an official declaration."
DEMO = "DEMO DATA: REDACTED REAL SHEETS AND SIMULATED RECORDS"
BANNED = ["winner", "leading", "projected", "fraud", "rigging", "vote now", "go and vote", "you should vote",
          "don't forget to vote", "elected"]
WEB = ROOT / "naijawatchtower" / "web"


@pytest.fixture
def site(env):
    """env plus simulated records on two Abia units and one redacted real sheet in Taraba."""
    with env["sessions"]() as db:
        for code in env["codes"][:2]:
            unit = db.execute(select(PollingUnit).where(PollingUnit.code == code)).scalar_one()
            add_sim_record(db, unit, reading_of(consistent_values()))
        db.commit()
    upload(env, env["codes"][-1], seed=5)                     # real_redacted unit
    env["app"].state.queue.drain()
    return env


def page(env, url, who=None):
    headers = dict(HTML)
    if who:
        headers.update(auth(env, who))
    return env["client"].get(url, headers=headers)


def sheet_ids(env):
    with env["sessions"]() as db:
        return {s.source: s.id for s in db.execute(select(ResultSheet)).scalars()}


def all_pages(env):
    ids = sheet_ids(env)
    code = env["codes"][0]
    return ["/", "/?layer=verified", "/?layer=discrepancies", "/?layer=accreditation&zone=South+East",
            "/state/001", "/state/034", "/lga/001-01", "/ward/001-01-01", f"/pu/{code}",
            f"/sheet/{ids['simulated']}", f"/sheet/{ids['real_redacted']}", "/discrepancies", "/audit",
            "/how-it-works", "/about", "/track?ref=12345", "/login", "/ussd-sim"]


def test_every_page_renders_with_both_banners(site):
    for url in all_pages(site):
        r = page(site, url)
        assert r.status_code == 200, (url, r.status_code)
        assert UNOFFICIAL in r.text and DEMO in r.text, url
    r = page(site, "/review", who="reviewer")
    assert r.status_code == 200 and UNOFFICIAL in r.text and DEMO in r.text


def test_no_banned_wording_in_templates_strings_or_pages(site):
    sources = list((WEB / "templates").glob("*.html")) + list((WEB / "i18n").glob("*.json"))
    for path in sources:
        text = path.read_text().lower()
        for word in BANNED:
            assert not re.search(rf"\b{re.escape(word)}\b", text), (path.name, word)
    for url in all_pages(site) + ["/map.svg"]:
        text = page(site, url).text.lower()
        for word in BANNED:
            assert not re.search(rf"\b{re.escape(word)}\b", text), (url, word)


def test_pidgin_draft_renders(site):
    site["client"].cookies.set("lang", "pcm")
    r = page(site, "/how-it-works")
    assert "Dem dey count votes" in r.text and UNOFFICIAL not in r.text
    assert "UNOFFICIAL" in r.text and DEMO in r.text
    site["client"].cookies.set("lang", "en")


def test_map_svg(site):
    r = site["client"].get("/map.svg?layer=coverage")
    svg = r.text
    assert r.headers["content-type"].startswith("image/svg+xml")
    regions = re.findall(r'<a href="/state/(\d{3})" aria-label="([^"]+)" class="region"[^>]*data-step="([^"]+)"', svg)
    assert len(regions) == 37
    steps = {code: step for code, _, step in regions}
    labels = {code: label for code, label, _ in regions}
    assert steps["001"] != "none" and steps["002"] == "0"                # 2/3 digitised vs 0/3
    assert labels["001"].startswith("Abia: 67% digitised")
    no_unit_states = [c for c in steps if c not in ("001", "002", "034")]
    assert all(steps[c] == "none" for c in no_unit_states)              # no data -> hatch
    assert svg.count('fill="url(#nodata)"') == len(no_unit_states)
    assert "DEMO DATA" in svg and 'class="watermark"' in svg
    assert "Party" not in svg and "party_" not in svg and "Row " not in svg
    assert len(svg.encode()) <= 300_000
    gz = site["client"].get("/map.svg", headers={"Accept-Encoding": "gzip"})
    assert gz.headers.get("content-encoding") == "gzip" and len(gzip.compress(svg.encode())) < 60_000
    for layer in ("verified", "discrepancies", "accreditation"):
        assert site["client"].get(f"/map.svg?layer={layer}").text.count('class="region"') == 37


def test_map_layers_never_use_party_data(site):
    from naijawatchtower.api import services
    with site["sessions"]() as db:
        for layer in ("coverage", "verified", "discrepancies", "accreditation"):
            for rec in services.state_records(db, layer):
                assert not any("party" in k.lower() for k in rec), rec.keys()
    source = (WEB / "mapsvg.py").read_text()
    assert "PARTY_NAMES" not in source and "party_fields" not in source and "party_" not in source
    dashboard = (WEB / "templates" / "dashboard.html").read_text()
    map_section = dashboard.split('class="map-wrap"')[1].split("</section>")[0]
    assert "party" not in map_section.lower()


def test_drill_down_chain(site):
    r = page(site, "/")
    assert 'href="/state/001"' in r.text
    r = page(site, "/state/001")
    lga = re.search(r'href="(/lga/001-\d\d)"', r.text).group(1)
    r = page(site, lga)
    ward = re.search(r'href="(/ward/001-\d\d-\d\d)"', r.text).group(1)
    r = page(site, ward)
    unit = re.search(r'href="(/pu/[\d-]+)"', r.text).group(1)
    r = page(site, unit)
    sheet = re.search(r'href="(/sheet/\d+)"', r.text).group(1)
    r = page(site, sheet)
    assert r.status_code == 200 and "Figures read from the sheet" in r.text
    panel = site["client"].get("/state/001/panel")
    assert panel.status_code == 200 and "/lga/001-01" in panel.text


def test_real_sheet_page_shows_rows_not_parties_and_no_party_totals(site):
    ids = sheet_ids(site)
    r = page(site, f"/sheet/{ids['real_redacted']}")
    assert "Row 1" in r.text and "Party A" not in r.text and "/sheets/" in r.text   # redacted image link
    r = page(site, "/state/034")
    assert "Per-party totals are not shown for real sheets." in r.text and "Party A" not in r.text
    r = page(site, f"/sheet/{ids['simulated']}")
    assert "SIMULATED RECORD – no image" in r.text and "Party B" in r.text


def test_verify_and_review_through_web_forms(site):
    c = site["client"]
    with site["sessions"]() as db:                              # the sheet of the citizen's own unit
        sid = db.execute(select(ResultSheet.id).where(
            ResultSheet.pu_id == site["unit_ids"][site["codes"][0]])).scalars().first()
        other = db.execute(select(ResultSheet.id).where(
            ResultSheet.pu_id == site["unit_ids"][site["codes"][1]])).scalars().first()
    c.cookies.set("nwt_session", site["tokens"]["citizen"])
    assert "only confirm or dispute the result of the unit you registered to" in c.get(
        f"/sheet/{other}", headers=HTML).text
    r = c.get(f"/sheet/{sid}", headers=HTML)
    assert "Does this match the sheet posted at your unit?" in r.text
    r = c.post(f"/sheet/{sid}/verify", data={"verdict": "dispute", "correction_field": "party_02",
                                            "correction_value": "211"}, headers=HTML, follow_redirects=False)
    assert r.status_code == 303 and "?ref=" in r.headers["location"]
    r = c.get(r.headers["location"], headers=HTML)
    assert "Recorded. Your reference is" in r.text
    c.cookies.set("nwt_session", site["tokens"]["reviewer"])
    assert f"/review/{sid}" in c.get("/review", headers=HTML).text
    r = c.post(f"/review/{sid}", data={"decision": "modify", "reason_code": "", "f_party_02": "211"},
               headers=HTML)
    assert r.status_code == 422
    r = c.post(f"/review/{sid}", data={"decision": "accept", "reason_code": "confirmed_as_written"},
               headers=HTML, follow_redirects=False)
    assert r.status_code == 303
    assert page(site, f"/sheet/{sid}").text.count("Checked by a reviewer") >= 1
    c.cookies.clear()


def test_upload_page_for_ingest_only(site):
    c = site["client"]
    assert c.get("/upload", headers=HTML, follow_redirects=False).status_code == 303   # signed out
    c.cookies.set("nwt_session", site["tokens"]["citizen"])
    assert c.get("/upload", headers=HTML, follow_redirects=False).status_code == 303
    c.cookies.set("nwt_session", site["tokens"]["ingest"])
    r = c.get("/upload", headers=HTML)
    assert r.status_code == 200 and site["codes"][-1] in r.text and UNOFFICIAL in r.text
    from tests.test_api import jpeg_bytes
    r = c.post("/upload", data={"pu_code": site["codes"][-1]}, files={"file": ("s.jpg", jpeg_bytes(11), "image/jpeg")},
               headers=HTML, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/sheet/")
    c.cookies.clear()


def test_login_flow_and_demo_accounts(site, caplog):
    c = site["client"]
    caplog.set_level("WARNING")
    r = c.post("/login/request", data={"phone": "08077776666", "next": "/"}, headers=HTML)
    assert "Code issued" in r.text and "demo" in r.text.lower()
    code = re.search(r"DEMO OTP for user \w+\.\.\.: (\d{6})", caplog.text).group(1)
    r = c.post("/login/verify", data={"phone": "08077776666", "otp": code, "next": "/about",
                                      "pu_code": site["codes"][0]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/about" and "nwt_session" in r.cookies
    r = c.post("/login/verify", data={"phone": "08077776666", "otp": "000000", "next": "//evil.example"},
               follow_redirects=False)
    assert r.status_code == 401
    c.cookies.clear()
