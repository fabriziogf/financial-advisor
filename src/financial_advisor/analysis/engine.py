"""Runs the checks (PRD §7 Step 3, F3.1–F3.10).

Each check is isolated: an exception in one becomes an ERROR observation instead of
aborting the run. A crash in the fee audit shouldn't hide an unclaimed employer match
— but the failure is reported, never swallowed, and `fa check` exits non-zero on it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .checks import (
    allocation,
    cash_drag,
    concentration,
    debt,
    emergency_fund,
    fees,
    insurance,
    location,
    savings,
    tax_advantaged,
)
from .model import Observation, Status
from .snapshot import Snapshot

__all__ = ["CHECKS", "run_checks"]

Check = Callable[[Snapshot], list[Observation]]

CHECKS: tuple[tuple[str, str, Check], ...] = (
    ("F3.1", "Emergency fund", emergency_fund.check),
    ("F3.2", "Cash drag", cash_drag.check),
    ("F3.3", "Tax-advantaged accounts", tax_advantaged.check),
    ("F3.4", "Asset allocation", allocation.check),
    ("F3.5", "Asset location", location.check),
    ("F3.6", "Fees", fees.check),
    ("F3.7", "Concentration", concentration.check),
    ("F3.8", "Debt", debt.check),
    ("F3.9", "Insurance coverage", insurance.check),
    ("F3.10", "Savings rate", savings.check),
)


def run_checks(snapshot: Snapshot, only: Iterable[str] | None = None) -> list[Observation]:
    wanted = set(only) if only else None
    if wanted:
        unknown = wanted - {check_id for check_id, _, _ in CHECKS}
        if unknown:
            raise ValueError(f"unknown check id(s): {', '.join(sorted(unknown))}")

    results: list[Observation] = []
    for check_id, title, check in CHECKS:
        if wanted and check_id not in wanted:
            continue
        try:
            results.extend(check(snapshot))
        except Exception as exc:  # noqa: BLE001 — isolation is the point; see module docstring
            results.append(
                Observation(
                    key=f"{check_id}.error",
                    check_id=check_id,
                    title=title,
                    status=Status.ERROR,
                    summary=f"The check failed: {type(exc).__name__}: {exc}",
                )
            )
    return results
