"""Replay stored readings by image SHA-256 (`VLM_BACKEND=cache`, CLAUDE.md 2 and 4.1).

Readings of redacted copies live in `data_redacted/vlm_cache/`; readings of originals
in `data_real/vlm_cache/`. Both are git-ignored (the former until D3 is confirmed).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from naijawatchtower.config import DATA_REAL, DATA_REDACTED
from naijawatchtower.ocr.schema import reading_from_json, reading_to_json
from naijawatchtower.ocr.types import SheetReading


def image_sha256(path: Path) -> str:
    """SHA-256 of an image file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CacheMiss(KeyError):
    """No stored reading for this image."""


class CacheReader:
    """A `SheetReader` that returns stored readings."""

    def __init__(self, dirs: list[Path]) -> None:
        self.dirs = [Path(d) for d in dirs]

    @classmethod
    def from_config(cls) -> "CacheReader":
        return cls([DATA_REDACTED / "vlm_cache", DATA_REAL / "vlm_cache"])

    def path_for(self, image_path: Path) -> Path | None:
        sha = image_sha256(image_path)
        for d in self.dirs:
            if (d / f"{sha}.json").exists():
                return d / f"{sha}.json"
        return None

    def read(self, image_path: Path) -> SheetReading:
        """Stored reading for the image, or CacheMiss."""
        path = self.path_for(image_path)
        if path is None:
            raise CacheMiss(f"no cached reading for {Path(image_path).name}")
        return reading_from_json(json.loads(path.read_text()))


def store_reading(image_path: Path, reading: SheetReading, cache_dir: Path) -> Path:
    """Write a validated reading to `<cache_dir>/<image_sha256>.json`."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{image_sha256(image_path)}.json"
    out.write_text(json.dumps(reading_to_json(reading)))
    return out
