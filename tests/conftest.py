"""Shared fixtures: a consistent sheet and a generated test-pattern image (never a real sheet)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from naijawatchtower.config import field_names


def consistent_values() -> dict[str, int | None]:
    """A hand-built sheet that satisfies R1-R7 (three party rows filled, rest blank)."""
    values: dict[str, int | None] = {n: None for n in field_names()}
    values.update(registered_voters=600, accredited_voters=412, ballots_issued=700,
                  ballots_unused=270, ballots_spoiled=18, votes_rejected=12,
                  total_valid_votes=400, total_used_ballots=430,
                  party_02=210, party_07=150, party_14=40, total_valid_votes_bottom=400)
    return values


@pytest.fixture
def sheet() -> dict[str, int | None]:
    return consistent_values()


@pytest.fixture
def pattern_image(tmp_path: Path) -> Path:
    """A plain test-pattern JPEG with EXIF data, standing in for an image file in tests."""
    rng = np.random.default_rng(0)
    arr = (rng.random((400, 300, 3)) * 255).astype("uint8")
    img = Image.fromarray(arr)
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    exif[0x0132] = "2026:11:09 10:00:00"  # DateTime
    path = tmp_path / "images" / "pattern.jpg"
    path.parent.mkdir(parents=True)
    img.save(path, quality=90, exif=exif)
    return path
