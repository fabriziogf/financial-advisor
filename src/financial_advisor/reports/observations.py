"""Plain-text rendering of observations for `fa check`."""

from __future__ import annotations

import textwrap
from collections.abc import Iterable
from datetime import date

from ..analysis.model import Observation, Status

__all__ = ["format_observations"]

WIDTH = 88

_GROUPS = (
    (Status.ERROR, "CHECKS THAT FAILED"),
    (Status.ATTENTION, "NEEDS ATTENTION"),
    (Status.INSUFFICIENT_DATA, "MISSING INFORMATION"),
    (Status.OK, "LOOKS FINE"),
    (Status.NOT_APPLICABLE, "NOT APPLICABLE"),
)


def _check_order(observation: Observation) -> tuple[int, ...]:
    return tuple(int(part) for part in observation.check_id.lstrip("F").split("."))


def _wrap(text: str, first: str, rest: str | None = None) -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=first,
        subsequent_indent=rest if rest is not None else first,
    ) or [first.rstrip()]


def _render(observation: Observation, *, verbose: bool, compact: bool) -> list[str]:
    indent = "      "
    if observation.status is Status.ATTENTION:
        head = f"  [{observation.severity.name}] {observation.check_id}  {observation.title}"
    else:
        head = f"  {observation.check_id}  {observation.title}"
    lines = [head, *_wrap(observation.summary, indent)]

    if not compact:
        if observation.annual_impact is not None:
            lines.append(f"{indent}≈ {observation.annual_impact.format()} per year")
        for fact in observation.facts:
            lines.extend(_wrap(f"{fact.label}: {fact.value}", f"{indent}• ", f"{indent}  "))
        for item in observation.detail:
            lines.extend(_wrap(item, f"{indent}  ", f"{indent}    "))
    for item in observation.missing:
        lines.extend(_wrap(item, f"{indent}→ ", f"{indent}  "))
    if verbose:
        if observation.inputs:
            lines.append(f"{indent}Based on:")
            for item in observation.inputs:
                lines.extend(_wrap(item, f"{indent}  - ", f"{indent}    "))
        if observation.assumptions:
            lines.append(f"{indent}Assumptions:")
            for item in observation.assumptions:
                lines.extend(_wrap(item, f"{indent}  - ", f"{indent}    "))
    if observation.professional_review and observation.status is not Status.NOT_APPLICABLE:
        lines.extend(
            _wrap(
                "Tax, insurance, or legal matter: confirm with a licensed professional "
                "before acting.",
                f"{indent}⚖ ",
                f"{indent}  ",
            )
        )
    lines.append("")
    return lines


def format_observations(
    observations: Iterable[Observation], *, as_of: date, verbose: bool = False
) -> str:
    observations = list(observations)
    lines = [f"Financial check-up as of {as_of.isoformat()}", "=" * WIDTH]

    for status, heading in _GROUPS:
        group = [o for o in observations if o.status is status]
        if not group:
            continue
        if status is Status.ATTENTION:
            group.sort(key=lambda o: (-o.severity, _check_order(o), o.key))
        else:
            group.sort(key=lambda o: (_check_order(o), o.key))
        lines += ["", f"{heading} ({len(group)})", "-" * WIDTH]
        compact = status in (Status.OK, Status.NOT_APPLICABLE) and not verbose
        for observation in group:
            lines.extend(_render(observation, verbose=verbose, compact=compact))

    lines.append("=" * WIDTH)
    lines.extend(
        _wrap(
            "These are observations about your recorded data measured against the rules in "
            "rules/ — not advice, and not a substitute for a licensed professional. "
            + (
                ""
                if verbose
                else "Run `fa check --verbose` to see the assumptions behind each figure."
            ),
            "",
        )
    )
    return "\n".join(lines)
