"""Constructors shared by the checks, so every Observation is built the same way."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from ...reports.net_worth import STALE_AFTER_DAYS
from ..model import Observation, Severity, Status
from ..snapshot import AccountState


def check_id_of(key: str) -> str:
    """'F3.3.match' -> 'F3.3'."""
    return ".".join(key.split(".")[:2])


def _build(key: str, title: str, status: Status, summary: str, **fields: Any) -> Observation:
    for name in ("facts", "inputs", "assumptions", "missing", "detail"):
        if name in fields:
            fields[name] = tuple(fields[name])
    return Observation(
        key=key, check_id=check_id_of(key), title=title, status=status, summary=summary, **fields
    )


def attention(key: str, title: str, severity: Severity, summary: str, **fields: Any) -> Observation:
    return _build(key, title, Status.ATTENTION, summary, severity=severity, **fields)


def ok(key: str, title: str, summary: str, **fields: Any) -> Observation:
    return _build(key, title, Status.OK, summary, **fields)


def insufficient(
    key: str, title: str, summary: str, missing: Iterable[str], **fields: Any
) -> Observation:
    return _build(key, title, Status.INSUFFICIENT_DATA, summary, missing=missing, **fields)


def not_applicable(key: str, title: str, summary: str, **fields: Any) -> Observation:
    return _build(key, title, Status.NOT_APPLICABLE, summary, **fields)


def names(states: Iterable[AccountState]) -> str:
    return ", ".join(state.name for state in states)


def stale_note(states: Iterable[AccountState], as_of: date) -> str | None:
    stale = [s for s in states if s.is_stale(as_of)]
    if not stale:
        return None
    listed = ", ".join(f"{s.name} ({s.balance_as_of})" for s in stale)
    return f"Balances older than {STALE_AFTER_DAYS} days are used as recorded: {listed}."
