"""Observations — what the engine produces (PRD §6).

An Observation is a machine-derived fact about your finances, computed by
deterministic code with no model involved. It is deliberately *not* a
recommendation: ranking, framing, and "what to do next" belong to M2 (F4.1–F4.3).
Keeping the two apart is what makes every figure here unit-testable against a
hand-computed fixture (R1) and traceable to its inputs (G4).

Three properties every check must honour, enforced in `Observation.__post_init__`
where they can be:

* **Say when it doesn't know (R5).** A check lacking an input returns
  INSUFFICIENT_DATA with `missing` naming exactly what to provide and how — never a
  guess dressed up as a finding.
* **Show the working (G4).** `inputs` and `assumptions` travel with the result.
* **Quantify only what is honestly quantifiable.** `annual_impact` is set when a
  dollars-per-year figure follows from the inputs without speculation (unclaimed
  match, interest paid, yield foregone) and left `None` otherwise. It seeds M2's
  ranking; a made-up number there would corrupt the ordering silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import IntEnum, StrEnum

from ..money import Money

__all__ = [
    "Status",
    "Severity",
    "Fact",
    "Observation",
    "fmt_pct",
    "fmt_rate",
    "fmt_points",
    "fmt_months",
    "fmt_number",
]


class Status(StrEnum):
    ATTENTION = "attention"
    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class Severity(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class Fact:
    label: str
    value: str


@dataclass(frozen=True)
class Observation:
    # Stable identifier, e.g. "F3.3.match". Survives rewording; M2's decision log
    # (F4.6) keys on it to stop re-raising something already declined.
    key: str
    check_id: str
    title: str
    status: Status
    summary: str
    severity: Severity = Severity.NONE
    facts: tuple[Fact, ...] = ()
    annual_impact: Money | None = None
    inputs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    # R4: tax, estate, and insurance findings flag for a licensed professional.
    professional_review: bool = False
    detail: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.status is Status.ATTENTION and self.severity is Severity.NONE:
            raise ValueError(f"{self.key}: ATTENTION requires a severity")
        if self.status is not Status.ATTENTION and self.severity is not Severity.NONE:
            raise ValueError(f"{self.key}: severity only applies to ATTENTION")
        if self.status is Status.INSUFFICIENT_DATA and not self.missing:
            raise ValueError(f"{self.key}: INSUFFICIENT_DATA must say what is missing")
        if self.annual_impact is not None and self.annual_impact.is_negative():
            raise ValueError(f"{self.key}: annual_impact is a magnitude; got negative")


def fmt_pct(ratio: Decimal, places: int = 1) -> str:
    """0.1234 -> '12.3%'."""
    quantum = Decimal(1).scaleb(-places)
    return f"{(ratio * 100).quantize(quantum, rounding=ROUND_HALF_UP)}%"


def fmt_rate(rate: Decimal) -> str:
    """Interest rates read better with two places: 0.0425 -> '4.25%'."""
    return fmt_pct(rate, places=2)


def fmt_points(delta: Decimal) -> str:
    """A difference between two shares, in percentage points: 0.042 -> '+4.2 pts'."""
    points = (delta * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{'+' if points > 0 else ''}{points} pts"


def fmt_months(months: Decimal) -> str:
    return str(months.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def fmt_number(value: Decimal) -> str:
    """Trailing zeros dropped without scientific notation: 6.0 -> '6', 10 -> '10'."""
    return format(value.normalize(), "f")
