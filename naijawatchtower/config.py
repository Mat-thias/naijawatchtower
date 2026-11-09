"""Paths and loaders for the versioned configuration files in `config/`.

The field list is defined only in `config/field_list.json`; every other module
gets field names from here and never hard-codes them.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_REAL = ROOT / "data_real"
DATA_REDACTED = ROOT / "data_redacted"


@lru_cache(maxsize=1)
def field_spec() -> dict[str, Any]:
    """Return the parsed `config/field_list.json`."""
    return json.loads((CONFIG_DIR / "field_list.json").read_text())


@lru_cache(maxsize=1)
def field_names() -> tuple[str, ...]:
    """All numeric field names in their fixed order (items 1-8, party rows, bottom total)."""
    spec = field_spec()
    names = [item["name"] for item in spec["items"]]
    names += party_fields()
    names.append(spec["bottom_total"]["name"])
    return tuple(names)


@lru_cache(maxsize=1)
def item_fields() -> tuple[str, ...]:
    """The eight numbered items, in form order."""
    return tuple(item["name"] for item in field_spec()["items"])


@lru_cache(maxsize=1)
def party_fields() -> tuple[str, ...]:
    """Party-row field names `party_01` ... `party_NN`."""
    spec = field_spec()
    return tuple(f"{spec['party_prefix']}{i:02d}" for i in range(1, spec["party_rows"] + 1))


def bottom_total_field() -> str:
    """Name of the 'TOTAL VALID VOTES' row below the party table."""
    return field_spec()["bottom_total"]["name"]


def max_value() -> int:
    """Largest value a field may hold."""
    return int(field_spec()["max_value"])


@lru_cache(maxsize=1)
def states() -> tuple[dict[str, Any], ...]:
    """The 36 states and the FCT from `config/states.json`."""
    return tuple(json.loads((CONFIG_DIR / "states.json").read_text())["states"])


def state_by_number(number: int) -> dict[str, Any]:
    """Look up a state record by its INEC number (1-37)."""
    for state in states():
        if state["number"] == number:
            return state
    raise KeyError(f"unknown state number {number}")


@lru_cache(maxsize=1)
def rules_config() -> dict[str, Any]:
    """Return the parsed `config/rules.yaml`."""
    return yaml.safe_load((CONFIG_DIR / "rules.yaml").read_text())
