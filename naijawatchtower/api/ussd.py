"""USSD channel for feature phones (CLAUDE.md 7).

`handle_ussd(text, msisdn, lang, db)` is a pure function of the inputs so far (`text`
is every input joined by `*`). It replays them through a small state machine, so a
resumed session continues where it left off and the handler is testable without HTTP.
Side effects (registration, verification) go through the same services as the web app
and are idempotent, so replaying a session cannot record anything twice.

Every screen is at most 160 characters including the "[DEMO]" tag.
"""

from __future__ import annotations

import copy
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from naijawatchtower.api import audit
from naijawatchtower.api.db import PollingUnit, ResultSheet, User
from naijawatchtower.api.i18n import USSD_LANGUAGES, t
from naijawatchtower.api.pipeline import DIGITISED, current_values
from naijawatchtower.api.security import phone_hash
from naijawatchtower.api.services import (ServiceError, case_status, latest_sheet, party_label,
                                          record_verification, unit_by_code, weights)
from naijawatchtower.config import item_fields, party_fields
from naijawatchtower.validate.routing import thresholds

MAX_LEN = 160
MAX_INVALID = 3
MAX_LINES = 5


@dataclass
class Ctx:
    """Everything a screen needs; `user` is None for unknown numbers."""

    db: Session
    phone_hash: str
    lang: str
    user: User | None
    _lines: list[tuple[str, str]] | None = field(default=None, repr=False)

    def tr(self, key: str, **kw: object) -> str:
        return t(self.lang, key, **kw)

    def unit(self) -> PollingUnit | None:
        return self.db.get(PollingUnit, self.user.pu_id) if self.user and self.user.pu_id else None

    def sheet(self) -> ResultSheet | None:
        unit = self.unit()
        return latest_sheet(self.db, unit.id) if unit else None

    def lines(self) -> list[tuple[str, str]]:
        """(field name, display line) for items 1-8 and every non-blank party row."""
        if self._lines is None:
            sheet, unit = self.sheet(), self.unit()
            cur = current_values(self.db, sheet.id)  # type: ignore[union-attr]
            conf_min = thresholds()["field_conf_min"]
            names = list(item_fields()) + [f for f in party_fields() if cur.get(f, {}).get("value") is not None]
            out = []
            for i, name in enumerate(names, 1):
                f = cur.get(name, {})
                label = party_label(unit, name) if name.startswith("party_") else self.tr(f"field.{name}")  # type: ignore[arg-type]
                low = f.get("source") == "model" and (f.get("confidence") or 0) < conf_min
                value = "-" if f.get("value") is None else str(f["value"])
                out.append((name, f"{i} {label} {value}{'?' if low else ''}"))
            self._lines = out
        return self._lines


class Screen:
    """A USSD screen. `consume` returns the next screen; `render` returns (CON|END, text)."""

    final = False

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.invalid = 0

    def consume(self, token: str) -> "Screen":
        raise NotImplementedError

    def body(self) -> str:
        raise NotImplementedError

    def render(self) -> tuple[str, str]:
        return ("END" if self.final else "CON"), self.prefix() + self.body()

    def prefix(self) -> str:
        return self.ctx.tr("ussd.invalid") if self.invalid else ""

    def retry(self) -> "Screen":
        if self.invalid + 1 >= MAX_INVALID:
            return End(self.ctx, self.ctx.tr("ussd.too_many_invalid"))
        again = copy.copy(self)
        again.invalid = self.invalid + 1
        return again

    def options(self) -> list[str]:
        """Sample inputs (valid and invalid) for exhaustive walking in tests."""
        return ["1", "2", "0", "8"]


class End(Screen):
    final = True

    def __init__(self, ctx: Ctx, text: str) -> None:
        super().__init__(ctx)
        self.text = text

    def body(self) -> str:
        return self.text

    def options(self) -> list[str]:
        return []


class Main(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.main")

    def options(self) -> list[str]:
        return ["1", "2", "3", "4", "9", "0", "8"]

    def consume(self, tok: str) -> Screen:
        c = self.ctx
        if tok == "0":
            return End(c, c.tr("ussd.goodbye"))
        if tok == "3":
            return Register(c)
        if tok == "4":
            return Language(c)
        if tok == "9":
            return More(c)
        if tok in ("1", "2"):
            if c.unit() is None:
                return NotRegistered(c)
            return check_screen(c) if tok == "1" else start_verify(c)
        return self.retry()


def check_screen(c: Ctx) -> Screen:
    unit, sheet = c.unit(), c.sheet()
    stage = c.tr(f"ussd.stage.{sheet.status}") if sheet else c.tr("ussd.stage.no_sheet")
    _, dispute, _ = weights(c.db, sheet.id) if sheet else (0, 0, False)
    return End(c, c.tr("ussd.check", code=unit.numeric_code, stage=stage, disputes=dispute))  # type: ignore[union-attr]


def start_verify(c: Ctx) -> Screen:
    unit, sheet = c.unit(), c.sheet()
    if sheet is None or sheet.status not in DIGITISED:
        return End(c, c.tr("ussd.verify_not_ready", code=unit.numeric_code))  # type: ignore[union-attr]
    from naijawatchtower.api.db import Verification
    done = c.db.execute(select(Verification).where(Verification.sheet_id == sheet.id,
                                                   Verification.user_id == c.user.id)).scalar_one_or_none()  # type: ignore[union-attr]
    if done is not None:
        return End(c, c.tr("ussd.already", ref=done.case_ref))
    return VerifyPage(c, 0)


class NotRegistered(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.not_registered")

    def options(self) -> list[str]:
        return ["3", "0", "8"]

    def consume(self, tok: str) -> Screen:
        if tok == "3":
            return Register(self.ctx)
        if tok == "0":
            return End(self.ctx, self.ctx.tr("ussd.goodbye"))
        return self.retry()


class Register(Screen):
    def prefix(self) -> str:
        return ""

    def body(self) -> str:
        return self.ctx.tr("ussd.reg_bad" if self.invalid else "ussd.reg_prompt")

    def options(self) -> list[str]:
        unit = self.ctx.db.execute(select(PollingUnit).limit(1)).scalar_one_or_none()
        return [unit.numeric_code if unit else "9240102003", "1234567890", "0"]

    def consume(self, tok: str) -> Screen:
        if tok == "0":
            return End(self.ctx, self.ctx.tr("ussd.goodbye"))
        unit = unit_by_code(self.ctx.db, tok) if tok.isdigit() and len(tok) == 10 else None
        return RegisterOptIn(self.ctx, unit) if unit else self.retry()


class RegisterOptIn(Screen):
    def __init__(self, ctx: Ctx, unit: PollingUnit) -> None:
        super().__init__(ctx)
        self.unit = unit

    def body(self) -> str:
        return self.ctx.tr("ussd.reg_optin", code=self.unit.numeric_code)

    def consume(self, tok: str) -> Screen:
        if tok not in ("1", "2"):
            return self.retry()
        c = self.ctx
        user = c.user or User(phone_hash=c.phone_hash, role="citizen", language=c.lang)
        if c.user is None:
            c.db.add(user)
        user.pu_id = self.unit.id
        user.opted_in_notifications = tok == "1"
        c.db.flush()
        c.user = user
        audit.append(c.db, user.role, str(user.id), "unit_registered",
                     {"unit": self.unit.code, "channel": "ussd", "sms_opt_in": tok == "1"})
        c.db.commit()
        return End(c, c.tr("ussd.reg_ok", code=self.unit.numeric_code))


class Language(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.lang_menu")

    def consume(self, tok: str) -> Screen:
        if tok not in USSD_LANGUAGES:
            return self.retry()
        c = self.ctx
        c.lang = USSD_LANGUAGES[tok]
        if c.user is None:
            c.user = User(phone_hash=c.phone_hash, role="citizen", language=c.lang, opted_in_notifications=False)
            c.db.add(c.user)
        c.user.language = c.lang
        c.db.commit()
        return End(c, c.tr("ussd.lang_set"))


class More(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.more")

    def options(self) -> list[str]:
        return ["5", "6", "7", "0", "8"]

    def consume(self, tok: str) -> Screen:
        c = self.ctx
        if tok == "5":
            if c.user is not None and c.user.opted_in_notifications:
                c.user.opted_in_notifications = False
                audit.append(c.db, c.user.role, str(c.user.id), "sms_opt_out", {"channel": "ussd"})
                c.db.commit()
            return End(c, c.tr("ussd.stop_alerts"))
        if tok == "6":
            return Track(c)
        if tok == "7":
            return How(c, 1)
        if tok == "0":
            return Main(c)
        return self.retry()


class Track(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.track_prompt")

    def options(self) -> list[str]:
        return ["99999"]

    def consume(self, tok: str) -> Screen:
        c = self.ctx
        case = case_status(c.db, tok)
        if case is None:
            return End(c, c.tr("ussd.track_unknown"))
        return End(c, c.tr("ussd.track_result", ref=case["ref"], status=c.tr(f"ussd.case.{case['status']}")))


class How(Screen):
    def __init__(self, ctx: Ctx, page: int) -> None:
        super().__init__(ctx)
        self.page = page
        self.final = page == 3

    def body(self) -> str:
        return self.ctx.tr(f"ussd.how_{self.page}")

    def consume(self, tok: str) -> Screen:
        return How(self.ctx, self.page + 1) if tok == "1" else self.retry()


def paginate(c: Ctx) -> list[list[tuple[str, str]]]:
    """Pack verify lines into pages of at most 5 lines and 160 characters (tag included)."""
    budget = MAX_LEN - len(c.tr("ussd.demo_tag")) - len(c.tr("ussd.invalid"))
    sheet_unit = c.unit()
    header = c.tr("ussd.verify_header", code=sheet_unit.numeric_code, page=99, pages=99)  # type: ignore[union-attr]
    fixed = len(header) + 1 + len(c.tr("ussd.nav")) + 1
    pages: list[list[tuple[str, str]]] = [[]]
    for line in c.lines():
        page = pages[-1]
        used = fixed + sum(len(x) + 1 for _, x in page)
        if len(page) >= MAX_LINES or used + len(line[1]) + 1 > budget:
            pages.append([line])
        else:
            page.append(line)
    return pages


class VerifyPage(Screen):
    def __init__(self, ctx: Ctx, page: int) -> None:
        super().__init__(ctx)
        self.page = page
        self.pages = paginate(ctx)

    def body(self) -> str:
        c = self.ctx
        header = c.tr("ussd.verify_header", code=c.unit().numeric_code, page=self.page + 1,  # type: ignore[union-attr]
                      pages=len(self.pages))
        lines = "\n".join(x for _, x in self.pages[self.page])
        return f"{header}\n{lines}\n{c.tr('ussd.nav')}"

    def options(self) -> list[str]:
        return ["1", "0", "8"]

    def consume(self, tok: str) -> Screen:
        if tok == "1":
            return VerifyPage(self.ctx, self.page + 1) if self.page + 1 < len(self.pages) else VerifyQuestion(self.ctx)
        if tok == "0":
            return VerifyPage(self.ctx, self.page - 1) if self.page > 0 else Main(self.ctx)
        return self.retry()


def _verify(c: Ctx, verdict: str, corrections: list[tuple[str, int | None]], ok_key: str) -> Screen:
    try:
        result = record_verification(c.db, c.user, c.sheet(), verdict, None, corrections, channel="ussd")  # type: ignore[arg-type]
        c.db.commit()
        return End(c, c.tr(ok_key, ref=result.case_ref))
    except ServiceError as err:
        c.db.rollback()
        if err.code == "already_recorded":
            return End(c, c.tr("ussd.already", ref=err.message.split("Ref ")[-1].rstrip(".")))
        return End(c, c.tr("ussd.error"))


class VerifyQuestion(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.verify_question")

    def consume(self, tok: str) -> Screen:
        if tok == "1":
            return _verify(self.ctx, "confirm", [], "ussd.verify_yes")
        if tok == "2":
            return LinePrompt(self.ctx)
        return self.retry()


class LinePrompt(Screen):
    def body(self) -> str:
        return self.ctx.tr("ussd.line_prompt", n=len(self.ctx.lines()))

    def options(self) -> list[str]:
        return ["1", str(len(self.ctx.lines())), "99", "x"]

    def consume(self, tok: str) -> Screen:
        if tok.isdigit() and 1 <= int(tok) <= len(self.ctx.lines()):
            return ValuePrompt(self.ctx, self.ctx.lines()[int(tok) - 1][0])
        return self.retry()


class ValuePrompt(Screen):
    def __init__(self, ctx: Ctx, field_name: str) -> None:
        super().__init__(ctx)
        self.field_name = field_name

    def body(self) -> str:
        return self.ctx.tr("ussd.value_prompt")

    def options(self) -> list[str]:
        return ["150", "12345", "abc"]

    def consume(self, tok: str) -> Screen:
        if tok.isdigit() and len(tok) <= 4:
            return _verify(self.ctx, "dispute", [(self.field_name, int(tok))], "ussd.verify_no")
        return self.retry()


def build_ctx(msisdn: str, lang: str, db: Session) -> Ctx:
    ph = phone_hash(msisdn)
    user = db.execute(select(User).where(User.phone_hash == ph)).scalar_one_or_none()
    return Ctx(db=db, phone_hash=ph, lang=user.language if user else lang, user=user)


def replay(text: str, ctx: Ctx) -> Screen:
    """Run every input in `text` through the state machine."""
    screen: Screen = Main(ctx)
    for tok in (text.split("*") if text else []):
        screen = screen.consume(tok.strip())
        if screen.final:
            break
    return screen


def handle_ussd(text: str, msisdn: str, lang: str, db: Session) -> tuple[str, str]:
    """(\"CON\" | \"END\", message) for the session so far. The message carries the [DEMO] tag."""
    ctx = build_ctx(msisdn, lang, db)
    kind, body = replay(text, ctx).render()
    return kind, ctx.tr("ussd.demo_tag") + body


def walk(msisdn: str, lang: str, fresh_session: Callable[[], AbstractContextManager[Session]],
         max_depth: int = 7) -> list[dict[str, Any]]:
    """Every screen reachable with each screen's sample inputs (for the 160-character test).

    `fresh_session` must give an isolated copy of the database for each replay, so the side
    effects of one branch (a recorded confirmation) do not hide another (the dispute path).
    """
    seen: list[dict[str, Any]] = []
    expanded: set[tuple[str, str]] = set()
    frontier = [""]
    for _ in range(max_depth + 1):
        nxt = []
        for text in frontier:
            with fresh_session() as db:
                ctx = build_ctx(msisdn, lang, db)
                screen = replay(text, ctx)
                kind, body = screen.render()
                message = ctx.tr("ussd.demo_tag") + body
                options = [] if screen.final else screen.options()
            seen.append({"text": text, "kind": kind, "message": message, "screen": type(screen).__name__})
            key = (type(screen).__name__, message)
            if options and key not in expanded:
                expanded.add(key)
                nxt += [f"{text}*{o}" if text else o for o in options]
        frontier = nxt
    return seen
