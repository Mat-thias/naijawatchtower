"""Database schema (CLAUDE.md 5.1): SQLite via SQLAlchemy 2.

No raw phone numbers, names or voter data are ever stored. Real unit codes never
enter this database: real sheets sit under pseudonymous `8NN` units.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    """Timezone-aware current time (stored as UTC)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class PollingUnit(Base):
    __tablename__ = "polling_unit"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    numeric_code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    state_code: Mapped[str] = mapped_column(String(3))           # INEC state number, e.g. "024"
    lga_code: Mapped[str] = mapped_column(String(2))
    ra_code: Mapped[str] = mapped_column(String(2))
    state_name: Mapped[str] = mapped_column(String(60))
    zone: Mapped[str] = mapped_column(String(20))
    lga_name: Mapped[str] = mapped_column(String(60))
    ward_name: Mapped[str] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(16))              # real_redacted | simulated


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(10), default="citizen")  # citizen|observer|reviewer|ingest|admin
    pu_id: Mapped[int | None] = mapped_column(ForeignKey("polling_unit.id"), nullable=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opted_in_notifications: Mapped[bool] = mapped_column(Boolean, default=True)


class ResultSheet(Base):
    __tablename__ = "result_sheet"
    id: Mapped[int] = mapped_column(primary_key=True)
    pu_id: Mapped[int] = mapped_column(ForeignKey("polling_unit.id"))
    source: Mapped[str] = mapped_column(String(16))              # real_redacted | simulated
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ingest_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="PROCESSING")
    pipeline_outcome: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pipeline_detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_revision: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)


class SheetJob(Base):
    __tablename__ = "sheet_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"))
    state: Mapped[str] = mapped_column(String(10), default="queued")   # queued|running|done|failed
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    vram_peak_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtractedField(Base):
    __tablename__ = "extracted_field"
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    unclear: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20))             # model | repair_suggestion | human_review
    reader: Mapped[str] = mapped_column(String(10))             # vlm | simulated | human
    alternatives_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValidationResult(Base):
    __tablename__ = "validation_result"
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(8))
    severity: Mapped[str] = mapped_column(String(6))
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text)


class Verification(Base):
    __tablename__ = "verification"
    __table_args__ = (UniqueConstraint("sheet_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    verdict: Mapped[str] = mapped_column(String(8))              # confirm | dispute
    weight: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(String(6), default="web")   # web | ussd
    case_ref: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Correction(Base):
    __tablename__ = "correction"
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(40))
    proposed_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposer_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    resolution: Mapped[str] = mapped_column(String(10), default="pending")   # accepted|rejected|pending
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    case_ref: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Discrepancy(Base):
    __tablename__ = "discrepancy"
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"), index=True)
    type: Mapped[str] = mapped_column(String(28))   # ARITHMETIC_INCONSISTENT|VERIFIER_DISPUTE|MODEL_VS_HUMAN_MISMATCH|UNREADABLE
    status: Mapped[str] = mapped_column(String(10), default="open")   # open | resolved | dismissed
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseRef(Base):
    __tablename__ = "case_ref"
    ref: Mapped[str] = mapped_column(String(8), primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey("result_sheet.id"))
    kind: Mapped[str] = mapped_column(String(12))           # confirmation | dispute | correction
    status: Mapped[str] = mapped_column(String(32), default="received")
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SmsOutbox(Base):
    __tablename__ = "sms_outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    body: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))                 # ISO timestamp, hashed as text
    actor_role: Mapped[str] = mapped_column(String(10))
    actor_ref: Mapped[str] = mapped_column(String(24))          # user id or "system"; never a phone number
    action: Mapped[str] = mapped_column(String(40))
    sheet_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


def make_engine(url: str):
    """Engine with SQLite foreign keys on and safe cross-thread use for the worker."""
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):  # noqa: ANN001
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine


def make_sessionmaker(url: str) -> sessionmaker[Session]:
    """Session factory bound to a new engine at `url`."""
    return sessionmaker(make_engine(url), expire_on_commit=False)
