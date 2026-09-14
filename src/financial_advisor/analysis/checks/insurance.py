"""F3.9 — Insurance coverage against rule-of-thumb heuristics.

Deliberately crude, and always flagged for professional review (R4). The value here
is noticing an obvious gap — no disability coverage on a household's only income —
not sizing a policy.
"""

from __future__ import annotations

from ..model import Fact, Observation, Severity
from ..snapshot import Snapshot
from ._common import attention, insufficient, ok

TITLE = "Insurance coverage"
KEY = "F3.9.coverage"


def check(snapshot: Snapshot) -> list[Observation]:
    profile = snapshot.profile
    if profile is None or not profile.insurance_stated:
        return [
            insufficient(
                KEY,
                TITLE,
                "Insurance coverage isn't recorded in your profile.",
                [
                    "Add an insurance section to your profile: life_coverage, "
                    "long_term_disability, umbrella_coverage."
                ],
                professional_review=True,
            )
        ]

    thresholds = snapshot.thresholds
    multiple = thresholds.integer("insurance", "life_income_multiple")
    findings: list[tuple[Severity, str]] = []
    missing: list[str] = []
    facts: list[Fact] = []

    # Life
    if profile.dependents is None:
        missing.append("Set household.dependents in your profile.")
    elif profile.dependents == 0:
        facts.append(Fact("Life insurance", "no dependents declared; not assessed"))
    elif profile.annual_salary is None:
        missing.append("Set income.annual_salary to size life-insurance need.")
    elif profile.life_coverage is None:
        missing.append("Set insurance.life_coverage in your profile (0 if none).")
    else:
        need = profile.annual_salary * multiple
        facts.append(Fact("Life coverage", f"{profile.life_coverage.format()} (heuristic: {need.format()})"))
        if profile.life_coverage < need:
            findings.append(
                (
                    Severity.MEDIUM,
                    f"Life coverage of {profile.life_coverage.format()} is below {multiple}× salary "
                    f"({need.format()}) with {profile.dependents} dependent(s).",
                )
            )

    # Disability
    if profile.long_term_disability is None:
        missing.append("Set insurance.long_term_disability (true or false).")
    else:
        facts.append(Fact("Long-term disability", "recorded" if profile.long_term_disability else "none recorded"))
        if not profile.long_term_disability and profile.annual_salary is not None:
            findings.append(
                (
                    Severity.MEDIUM,
                    f"No long-term disability coverage is recorded, while salary of "
                    f"{profile.annual_salary.format()} a year depends on being able to work.",
                )
            )

    # Umbrella
    floor = thresholds.money("insurance", "umbrella_net_worth_floor")
    net_worth = snapshot.net_worth()
    if net_worth >= floor:
        if profile.umbrella_coverage is None:
            missing.append("Set insurance.umbrella_coverage (0 if none).")
        else:
            facts.append(Fact("Umbrella coverage", profile.umbrella_coverage.format()))
            if profile.umbrella_coverage < net_worth:
                findings.append(
                    (
                        Severity.LOW,
                        f"Umbrella liability coverage of {profile.umbrella_coverage.format()} is below "
                        f"recorded net worth of {net_worth.format()}.",
                    )
                )

    assumptions = [
        f"Life-insurance need uses a {multiple}× salary rule of thumb. It's crude by design: it "
        "ignores savings, a partner's income, debts, and education goals.",
        "Coverage through an employer counts only if it's included in the figures you recorded.",
        "Policies aren't priced or compared, and definitions of disability differ between policies.",
        f"The umbrella comparison applies once recorded net worth reaches {floor.format()}.",
    ]
    inputs = ("insurance section of your profile", "household.dependents", "income.annual_salary", "Net worth")

    if findings:
        findings.sort(key=lambda f: -f[0])
        summary = (
            findings[0][1]
            if len(findings) == 1
            else f"{len(findings)} possible coverage gaps against rule-of-thumb heuristics."
        )
        return [
            attention(
                KEY,
                TITLE,
                findings[0][0],
                summary,
                facts=facts,
                detail=[text for _, text in findings],
                inputs=inputs,
                assumptions=assumptions,
                missing=missing,
                professional_review=True,
            )
        ]
    if missing:
        return [
            insufficient(
                KEY,
                TITLE,
                "Coverage can't be fully assessed yet.",
                missing,
                facts=facts,
                inputs=inputs,
                assumptions=assumptions,
                professional_review=True,
            )
        ]
    return [
        ok(
            KEY,
            TITLE,
            "No gaps against the rule-of-thumb heuristics.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
            professional_review=True,
        )
    ]
