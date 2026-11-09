"""Strict output schema and (de)serialisation of readings (CLAUDE.md 4.2).

`parse_output` validates the reader's JSON: exactly the 27 field keys, each an integer
0-9999 or null, plus an `unclear` list of field names; extra keys are rejected.
Only validated content is ever stored; the raw model text never is.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator

from naijawatchtower.config import field_names, max_value
from naijawatchtower.ocr.types import FieldReading, SheetReading


class SchemaError(ValueError):
    """The output does not match the schema."""


@lru_cache(maxsize=1)
def _models() -> tuple[type[BaseModel], type[BaseModel]]:
    value = (int | None, Field(..., ge=0, le=max_value(), strict=True))
    fields_model = create_model("SheetFields", __config__=ConfigDict(extra="forbid"),
                                **{n: value for n in field_names()})  # type: ignore[call-overload]
    names = set(field_names())

    class Output(BaseModel):
        model_config = ConfigDict(extra="forbid")
        fields: fields_model  # type: ignore[valid-type]
        unclear: list[str]

        @field_validator("unclear")
        @classmethod
        def known_names(cls, v: list[str]) -> list[str]:
            unknown = [n for n in v if n not in names]
            if unknown:
                raise ValueError(f"unknown field names in unclear: {unknown}")
            return list(dict.fromkeys(v))

    return fields_model, Output


def parse_output(text: str) -> tuple[dict[str, int | None], list[str]]:
    """Validate the reader's JSON text. Returns (values, unclear) or raises SchemaError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise SchemaError(f"not JSON: {err}") from None
    try:
        out = _models()[1].model_validate(data)
    except ValidationError as err:
        raise SchemaError(str(err)) from None
    return out.fields.model_dump(), out.unclear  # type: ignore[attr-defined]


def to_output_json(values: dict[str, int | None], unclear: list[str] | None = None) -> str:
    """Compact output JSON in the fixed field order (used for few-shot exemplar answers)."""
    fields = ",".join(f'"{n}":{"null" if values.get(n) is None else int(values[n])}' for n in field_names())
    names = ",".join(f'"{n}"' for n in (unclear or []))
    return '{"fields":{' + fields + '},"unclear":[' + names + "]}"


def reading_to_json(reading: SheetReading) -> dict[str, Any]:
    """JSON-serialisable form of a reading (values, confidences, alternatives; no raw text)."""
    return {
        "fields": {n: {"value": f.value, "unclear": f.unclear, "confidence": f.confidence,
                       "alternatives": [[v, p] for v, p in f.alternatives]}
                   for n, f in reading.fields.items()},
        "model_revision": reading.model_revision, "prompt_version": reading.prompt_version,
        "seconds": reading.seconds, "vram_peak_mb": reading.vram_peak_mb, "json_valid": reading.json_valid,
        "extra": reading.extra,
    }


def reading_from_json(data: dict[str, Any]) -> SheetReading:
    """Inverse of `reading_to_json`; validates field names and value ranges."""
    names = set(field_names())
    if set(data["fields"]) != names:
        raise SchemaError("cached reading does not have exactly the 27 fields")
    fields = {}
    for n, f in data["fields"].items():
        v = f["value"]
        if v is not None and (not isinstance(v, int) or not 0 <= v <= max_value()):
            raise SchemaError(f"{n}: value out of range")
        fields[n] = FieldReading.of(v, float(f["confidence"]), bool(f["unclear"]),
                                    tuple((a, float(p)) for a, p in f["alternatives"]))
    return SheetReading(fields=fields, model_revision=data.get("model_revision", ""),
                        prompt_version=data.get("prompt_version", ""), seconds=data.get("seconds", 0.0),
                        vram_peak_mb=data.get("vram_peak_mb", 0.0), json_valid=data.get("json_valid", True),
                        extra=data.get("extra", {}))
