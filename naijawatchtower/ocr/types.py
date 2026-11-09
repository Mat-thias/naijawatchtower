"""The `SheetReader` interface and the data types every reader returns (CLAUDE.md 4.1).

The rest of the system depends only on this module, never on a concrete reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from naijawatchtower.config import field_names


@dataclass(frozen=True)
class FieldReading:
    """One field as read from a sheet.

    `value` is None for a blank cell. `alternatives` holds other candidate
    readings with their probabilities, most probable first.
    """

    value: int | None
    is_blank: bool
    unclear: bool = False
    confidence: float = 1.0
    alternatives: tuple[tuple[int | None, float], ...] = ()

    @staticmethod
    def of(value: int | None, confidence: float = 1.0, unclear: bool = False,
           alternatives: tuple[tuple[int | None, float], ...] = ()) -> "FieldReading":
        """Build a reading, deriving `is_blank` from `value`."""
        return FieldReading(value=value, is_blank=value is None, unclear=unclear,
                            confidence=confidence, alternatives=alternatives)


@dataclass
class SheetReading:
    """All 27 fields of one sheet plus provenance."""

    fields: dict[str, FieldReading]
    model_revision: str = ""
    prompt_version: str = ""
    seconds: float = 0.0
    vram_peak_mb: float = 0.0
    json_valid: bool = True
    extra: dict[str, object] = field(default_factory=dict)

    def values(self) -> dict[str, int | None]:
        """Top-1 value of every field."""
        return {name: reading.value for name, reading in self.fields.items()}

    def unclear_fields(self) -> list[str]:
        """Fields the reader marked as unclear, in field order."""
        return [n for n in field_names() if n in self.fields and self.fields[n].unclear]


class SheetReader(Protocol):
    """Anything that can turn a sheet image into a `SheetReading`."""

    def read(self, image_path: Path) -> SheetReading:
        """Read all fields from the image at `image_path`."""
        ...


class FakeReader:
    """Test reader that returns preset values, keyed by image file name.

    Unknown images read as all-blank with full confidence.
    """

    def __init__(self, by_name: dict[str, dict[str, int | None]] | None = None,
                 confidence: float = 0.99) -> None:
        self.by_name = by_name or {}
        self.confidence = confidence

    def read(self, image_path: Path) -> SheetReading:
        """Return the preset reading for `image_path.name`."""
        preset = self.by_name.get(Path(image_path).name, {})
        fields = {n: FieldReading.of(preset.get(n), self.confidence) for n in field_names()}
        return SheetReading(fields=fields, model_revision="fake", prompt_version="fake")
