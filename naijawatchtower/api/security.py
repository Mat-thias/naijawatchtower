"""Secrets, phone hashing, sessions, the OTP stub and rate limits (CLAUDE.md 5.1, 5.3, 7).

- Raw phone numbers are hashed with HMAC-SHA256(SERVER_SECRET, normalised number) and
  never stored or logged.
- Sessions are stateless signed tokens (user id + expiry).
- The web OTP is a PoC stub: the code is printed to the server log (keyed by a hash prefix).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field

log = logging.getLogger("naijawatchtower")

DEV_SERVER_SECRET = "dev-only-server-secret-change-me"
DEV_USSD_SECRET = "dev-only-ussd-secret"


def _secret(name: str, default: str) -> str:
    value = os.environ.get(name)
    if not value:
        warnings.warn(f"{name} not set; using an insecure development default", stacklevel=2)
        return default
    return value


def server_secret() -> str:
    """SERVER_SECRET from the environment (dev default with a warning)."""
    return _secret("SERVER_SECRET", DEV_SERVER_SECRET)


def ussd_secret() -> str:
    """USSD_SECRET (expected in the X-USSD-Secret header) from the environment."""
    return _secret("USSD_SECRET", DEV_USSD_SECRET)


def normalise_phone(raw: str) -> str:
    """Canonical Nigerian MSISDN: digits only, leading 0 or +234 mapped to 234."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    if not re.fullmatch(r"\d{10,15}", digits):
        raise ValueError("not a phone number")
    return digits


def phone_hash(raw: str, secret: str | None = None) -> str:
    """HMAC-SHA256 of the normalised phone number; the only form in which a number is kept."""
    key = (secret or server_secret()).encode()
    return hmac.new(key, normalise_phone(raw).encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ sessions

def make_token(user_id: int, ttl_seconds: int = 12 * 3600, secret: str | None = None) -> str:
    """Signed session token."""
    body = f"{user_id}.{int(time.time()) + ttl_seconds}"
    sig = hmac.new((secret or server_secret()).encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def read_token(token: str, secret: str | None = None) -> int | None:
    """User id from a valid, unexpired token; None otherwise."""
    try:
        uid, exp, sig = token.split(".")
    except (ValueError, AttributeError):
        return None
    good = hmac.new((secret or server_secret()).encode(), f"{uid}.{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return int(uid)


# ------------------------------------------------------------------ OTP stub

@dataclass
class OtpStore:
    """In-memory OTP codes keyed by phone hash (PoC stub; codes go to the server log)."""

    ttl_seconds: int = 300
    codes: dict[str, tuple[str, float]] = field(default_factory=dict)

    def issue(self, ph: str) -> str:
        code = f"{secrets.randbelow(10**6):06d}"
        self.codes[ph] = (code, time.time() + self.ttl_seconds)
        log.warning("DEMO OTP for user %s...: %s (simulated; no SMS is sent)", ph[:8], code)
        return code

    def check(self, ph: str, code: str) -> bool:
        stored = self.codes.get(ph)
        if not stored or stored[1] < time.time() or not hmac.compare_digest(stored[0], code):
            return False
        del self.codes[ph]
        return True


# ------------------------------------------------------------------ rate limits

@dataclass
class TokenBucket:
    """In-memory token buckets: `capacity` requests per `period` seconds per key."""

    capacity: int
    period: float
    state: dict[str, tuple[float, float]] = field(default_factory=lambda: defaultdict(lambda: (0.0, 0.0)))

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        tokens, last = self.state.get(key, (float(self.capacity), now))
        tokens = min(self.capacity, tokens + (now - last) * self.capacity / self.period)
        if tokens < 1:
            self.state[key] = (tokens, now)
            return False
        self.state[key] = (tokens - 1, now)
        return True
