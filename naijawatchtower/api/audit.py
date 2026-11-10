"""Tamper-evident, append-only audit log (CLAUDE.md 5.2).

entry_hash = SHA256(prev_hash || ts || actor_role || action || payload_hash), genesis prev_hash = 64 zeros.
Payloads are hashed, never stored; they must not contain phone numbers or names.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from naijawatchtower.api.db import AuditLog, utcnow

GENESIS = "0" * 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def entry_hash(prev_hash: str, ts: str, actor_role: str, action: str, payload_hash: str) -> str:
    """Hash of one entry, chained to the previous one."""
    return _sha(prev_hash + ts + actor_role + action + payload_hash)


def append(db: Session, actor_role: str, actor_ref: str, action: str, payload: dict[str, Any],
           sheet_id: int | None = None) -> AuditLog:
    """Append an entry (caller commits)."""
    last = db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.entry_hash if last else GENESIS
    ts = utcnow().isoformat()
    ph = _sha(json.dumps(payload, sort_keys=True, default=str))
    row = AuditLog(ts=ts, actor_role=actor_role, actor_ref=str(actor_ref), action=action, sheet_id=sheet_id,
                   payload_hash=ph, prev_hash=prev, entry_hash=entry_hash(prev, ts, actor_role, action, ph))
    db.add(row)
    db.flush()
    return row


def verify_chain(db: Session) -> dict[str, Any]:
    """Recompute the chain. Returns {ok, first_broken_id, entries}."""
    prev = GENESIS
    rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
    for row in rows:
        expected = entry_hash(prev, row.ts, row.actor_role, row.action, row.payload_hash)
        if row.prev_hash != prev or row.entry_hash != expected:
            return {"ok": False, "first_broken_id": row.id, "entries": len(rows)}
        prev = row.entry_hash
    return {"ok": True, "first_broken_id": None, "entries": len(rows)}
