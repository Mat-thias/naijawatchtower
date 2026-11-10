"""Helpers to load units, simulated records and demo accounts into the database."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from naijawatchtower.api import audit
from naijawatchtower.api.db import PollingUnit, ResultSheet, User
from naijawatchtower.api.pipeline import apply_reading
from naijawatchtower.api.security import phone_hash
from naijawatchtower.config import state_by_number
from naijawatchtower.ocr.types import SheetReading
from naijawatchtower.sim.election import SimUnit

# Demo accounts (fictional numbers in the 0800 range); quick sign-in only when NWT_DEMO_MODE=1.
DEMO_PHONES = {"citizen": "08000000101", "citizen_2": "08000000102", "citizen_3": "08000000103",
               "observer": "08000000201", "reviewer": "08000000301", "ingest": "08000000401",
               "admin": "08000000501"}


def add_sim_unit(db: Session, u: SimUnit) -> PollingUnit:
    """Insert a simulated unit (9NN code)."""
    unit = PollingUnit(code=u.code, numeric_code=u.numeric_code, name=u.name, state_code=f"{u.state_number:03d}",
                       lga_code=u.lga_code, ra_code=u.ra_code, state_name=u.state_name, zone=u.zone,
                       lga_name=u.lga_name, ward_name=u.ward_name, source="simulated")
    db.add(unit)
    db.flush()
    return unit


def add_real_unit(db: Session, state_number: int, index: int) -> PollingUnit:
    """A pseudonymous unit for a redacted real sheet: 8NN code, sequential numbers, no real names (D5)."""
    st = state_by_number(state_number)
    lga, ra, pu = 1, 1, index
    numeric = f"8{state_number:02d}{lga:02d}{ra:02d}{pu:03d}"
    unit = PollingUnit(code=f"8{state_number:02d}-{lga:02d}-{ra:02d}-{pu:03d}", numeric_code=numeric,
                       name=f"Redacted unit {pu:02d}", state_code=f"{state_number:03d}", lga_code=f"{lga:02d}",
                       ra_code=f"{ra:02d}", state_name=st["name"], zone=st["zone"], lga_name="Redacted LGA 01",
                       ward_name="Redacted ward 01", source="real_redacted")
    db.add(unit)
    db.flush()
    return unit


def add_sim_record(db: Session, unit: PollingUnit, reading: SheetReading) -> ResultSheet:
    """A simulated record: no image, the simulated reading goes through the normal pipeline."""
    sheet = ResultSheet(pu_id=unit.id, source="simulated", status="PROCESSING")
    db.add(sheet)
    db.flush()
    audit.append(db, "system", "simulator", "sheet_uploaded", {"sheet": sheet.id, "unit": unit.code,
                                                              "simulated": True}, sheet.id)
    apply_reading(db, sheet, reading, "simulated")
    return sheet


def add_user(db: Session, phone: str, role: str = "citizen", unit: PollingUnit | None = None,
             opted_in: bool = False) -> User:
    """Create (or update) an account by phone hash."""
    ph = phone_hash(phone)
    user = db.execute(select(User).where(User.phone_hash == ph)).scalar_one_or_none()
    if user is None:
        user = User(phone_hash=ph, role=role)
        db.add(user)
    user.role, user.pu_id, user.opted_in_notifications = role, unit.id if unit else None, opted_in
    db.flush()
    return user
