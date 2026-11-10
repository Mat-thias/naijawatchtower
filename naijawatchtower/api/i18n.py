"""Translations (CLAUDE.md 7, 9): every user-facing string lives in `naijawatchtower/web/i18n/<lang>.json`.

Missing or empty strings fall back to English, so the empty Hausa, Yoruba and Igbo
files work until native speakers fill them in.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent.parent / "web" / "i18n"
LANGUAGES = ("en", "pcm", "ha", "yo", "ig")
USSD_LANGUAGES = {"1": "en", "2": "pcm"}


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    """All strings for one language."""
    path = I18N_DIR / f"{lang}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def t(lang: str, key: str, **kwargs: object) -> str:
    """Translated string for `key`, formatted with `kwargs`; English when missing or empty."""
    text = catalog(lang if lang in LANGUAGES else "en").get(key) or catalog("en").get(key)
    if text is None:
        raise KeyError(f"missing translation key {key!r}")
    return text.format(**kwargs) if kwargs else text
