"""Regenerate the README screenshots in docs/img/ from the demo (simulated records only).

Needs the demo server running (default http://127.0.0.1:8765, seeded with scripts/seed_demo.py)
and Firefox. The USSD image is drawn from the real USSD handler's output on a throwaway copy
of the demo database, so the screens shown are exactly what a phone would display.

Usage: python scripts/make_screenshots.py [--base http://127.0.0.1:8765]
"""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naijawatchtower.config import ROOT  # noqa: E402

IMG = ROOT / "docs" / "img"
WORK = ROOT / "shots"                       # git-ignored; Firefox (snap) can read it
USSD_PHONE = "08051234567"
USSD_UNIT = "9020102002"

PAGES = [  # (file, window size, path)
    ("01-dashboard.png", "1280,1250", "/"),
    ("02-sheet-suggestion.png", "1280,1900", "/sheet/30"),
    ("03-review-queue.png", "1280,1100", "/login/demo/reviewer?next=/review"),
    ("04-discrepancies.png", "1280,1000", "/discrepancies?type=ARITHMETIC_INCONSISTENT"),
    ("05-mobile.png", "390,1400", "/"),
    ("07-map-verified-layer.png", "1280,1000", "/?layer=verified"),
    ("08-state-drilldown.png", "1280,1300", "/state/012"),
    ("09-citizen-verify.png", "1280,2300", "/login/demo/citizen?next=/sheet/1"),
    ("10-audit-log.png", "1280,1000", "/audit"),
    ("11-explainer-pidgin.png", "1280,1050", "/how-it-works?lang=pcm"),
]


def ussd_screens() -> list[tuple[str, str, str]]:
    """(caption, input typed, screen text) for a register-then-verify session, from the real handler."""
    from naijawatchtower.api.db import make_sessionmaker
    from naijawatchtower.api.ussd import handle_ussd
    tmp = Path(tempfile.mkdtemp())
    shutil.copyfile(ROOT / "data" / "demo.db", tmp / "u.db")
    steps = [("Dial *123#", "", ""), ("Register", "3", "3"), ("Unit code", USSD_UNIT, f"3*{USSD_UNIT}"),
             ("SMS opt-in", "1", f"3*{USSD_UNIT}*1"), ("New session: verify", "2", "2")]
    with make_sessionmaker(f"sqlite:///{tmp}/u.db")() as db:
        out = []
        for caption, typed, text in steps:
            out.append((caption, typed, handle_ussd(text, USSD_PHONE, "en", db)))
        text = "2"
        while "Does this match" not in handle_ussd(text, USSD_PHONE, "en", db)[1]:
            text += "*1"
            out.append(("Next page", "1", handle_ussd(text, USSD_PHONE, "en", db)))
        out[-1] = ("Does it match?", "1", handle_ussd(text, USSD_PHONE, "en", db))
        out.append(("Confirmed", "1", handle_ussd(text + "*1", USSD_PHONE, "en", db)))
    return [(c, t, f"{k} {m}") for c, t, (k, m) in out]


def ussd_html(screens: list[tuple[str, str, str]]) -> str:
    cards = []
    for i, (caption, typed, raw) in enumerate(screens, 1):
        kind, body = raw[:3], raw[4:]
        cards.append(
            f'<figure><div class="phone"><div class="screen">{html.escape(body)}</div>'
            f'<div class="meta"><span>{kind}</span><span class="{"over" if len(body) > 160 else ""}">'
            f'{len(body)}/160</span></div></div><figcaption><b>{i}. {html.escape(caption)}</b>'
            f'{f" · typed <code>{html.escape(typed)}</code>" if typed else ""}</figcaption></figure>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;padding:22px;background:#f6f7f4;font:15px system-ui,sans-serif;color:#1a1c19}}
h1{{font-size:20px;margin:0 0 4px}} p{{margin:0 0 16px;color:#53594f}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}}
figure{{margin:0}} .phone{{background:#22252a;border-radius:22px;padding:16px 12px 10px}}
.screen{{background:#c9d8b4;color:#141a0e;border-radius:6px;min-height:200px;padding:9px;
 font:13.5px/1.35 ui-monospace,monospace;white-space:pre-wrap;word-break:break-word}}
.meta{{display:flex;justify-content:space-between;color:#d7dbd2;font:12px ui-monospace,monospace;margin-top:6px}}
.over{{color:#ff8a80}} figcaption{{margin-top:6px;font-size:13.5px}} code{{background:#e4e7df;padding:0 4px;border-radius:4px}}
</style></head><body><h1>USSD session on a basic phone (simulated demo data)</h1>
<p>Real screens from the USSD handler: register to a polling unit, read its figures, confirm. Every screen ≤ 160 characters.</p>
<div class="grid">{''.join(cards)}</div></body></html>"""


def firefox(out: Path, size: str, url: str) -> None:
    profile = WORK / "ff-profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.run(["firefox", "--headless", "--no-remote", "--profile", str(profile), f"--window-size={size}",
                    "--screenshot", str(out), url], capture_output=True, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    IMG.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(exist_ok=True)
    screens = ussd_screens()
    page = WORK / "ussd_flow.html"
    page.write_text(ussd_html(screens))
    rows = (len(screens) + 3) // 4
    firefox(IMG / "06-ussd-flow.png", f"1280,{130 + rows * 300}", page.as_uri())
    for name, size, path in PAGES:
        firefox(IMG / name, size, args.base + path)
    for p in sorted(IMG.glob("*.png")):
        print(f"{p.name}: {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
