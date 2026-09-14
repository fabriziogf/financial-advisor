"""F3.3 — Tax-advantaged space: employer match, workplace plan, IRA, and HSA.

The employer match comes first for a reason the PRD states plainly: unclaimed match
is the highest-certainty return in the whole system. It is employer money forgone
at a known rate, with no market assumption involved.
"""

from __future__ import annotations

from ...profile import Profile
from ...rules import ContributionLimits
from ..model import Fact, Observation, Severity, fmt_pct
from ..snapshot import Snapshot
from ._common import attention, insufficient, not_applicable, ok

GROUP_TITLE = "Tax-advantaged accounts"
TITLE_MATCH = "Employer match"
TITLE_PLAN = "Workplace plan contributions"
TITLE_IRA = "IRA contributions"
TITLE_HSA = "HSA contributions"


def check(snapshot: Snapshot) -> list[Observation]:
    profile = snapshot.profile
    year = snapshot.as_of.year
    if profile is None:
        return [
            insufficient(
                "F3.3.profile",
                GROUP_TITLE,
                "No profile exists, so contribution rates, the employer match, and HSA "
                "enrollment are unknown.",
                [
                    "Create your profile with `fa profile init`, then fill in employer_plan, "
                    "ira, hsa, and household.birth_year."
                ],
            )
        ]
    limits = snapshot.rules.limits
    if limits is None:
        return [
            insufficient(
                "F3.3.limits",
                GROUP_TITLE,
                snapshot.rules.limits_error or f"No contribution limits are on file for {year}.",
                [f"Add rules/limits/{year}.yml with the IRS's published {year} limits."],
            )
        ]
    results = [
        _match(snapshot, profile, limits),
        _plan(snapshot, profile, limits),
        _ira(snapshot, profile, limits),
        _hsa(snapshot, profile, limits),
    ]
    return [r for r in results if r is not None]


def _age_context(profile: Profile, year: int) -> tuple[int, list[str], list[str]]:
    """Age for limit lookups, plus assumption and missing notes when birth year is unknown."""
    age = profile.age_in(year)
    if age is None:
        return (
            0,
            ["Catch-up amounts aren't included because household.birth_year isn't set."],
            ["Set household.birth_year to include age-based catch-up limits."],
        )
    return age, [], []


def _match(snapshot: Snapshot, profile: Profile, limits: ContributionLimits) -> Observation:
    key = "F3.3.match"
    year = snapshot.as_of.year
    if not profile.employer_plan_stated:
        return insufficient(
            key,
            TITLE_MATCH,
            "Your profile doesn't say whether you have a workplace retirement plan.",
            [
                "Add an employer_plan section to your profile, or `employer_plan: null` "
                "if you have none."
            ],
        )
    plan = profile.employer_plan
    if plan is None:
        return not_applicable(
            key, TITLE_MATCH, "No workplace retirement plan (employer_plan: null)."
        )
    if not plan.match:
        return not_applicable(
            key, TITLE_MATCH, "Your workplace plan has no employer match recorded."
        )

    missing = []
    if profile.annual_salary is None:
        missing.append("Set income.annual_salary in your profile.")
    if plan.contribution_rate is None:
        missing.append("Set employer_plan.contribution_percent in your profile.")
    if missing or profile.annual_salary is None or plan.contribution_rate is None:
        return insufficient(
            key,
            TITLE_MATCH,
            "The match can't be computed without salary and contribution rate.",
            missing,
        )

    salary, rate = profile.annual_salary, plan.contribution_rate
    eligible_pay = min(salary, limits.compensation_limit)
    full = eligible_pay * plan.max_match_rate()
    earned = eligible_pay * plan.match_rate_at(rate)
    unclaimed = full - earned

    facts = [
        Fact("Your contribution", fmt_pct(rate)),
        Fact("Contribution needed for the full match", fmt_pct(plan.full_match_contribution)),
        Fact("Match earned", f"{earned.format()}/yr"),
        Fact("Full match available", f"{full.format()}/yr"),
    ]
    assumptions = [
        "The match is computed on base salary. Bonuses, and plans that define eligible pay "
        "differently, aren't modeled.",
        "Vesting isn't modeled: unvested match is forfeited if you leave before it vests.",
    ]
    if salary > limits.compensation_limit:
        assumptions.append(
            f"Pay above the {year} compensation limit of {limits.compensation_limit.format()} "
            "doesn't count toward employer contributions (IRC 401(a)(17))."
        )
    age, age_notes, _ = _age_context(profile, year)
    deferral_limit = limits.deferral_limit(age)
    elected = salary * rate
    if elected > deferral_limit:
        reduced = earned * deferral_limit.ratio_to(elected)
        facts.append(Fact("Match if contributions stop at the limit", f"{reduced.format()}/yr"))
        assumptions.append(
            f"At {fmt_pct(rate)} you'd reach the {deferral_limit.format()} limit before year end. "
            "Plans that match each paycheck without a year-end true-up stop matching then, "
            f"which would pay about {reduced.format()} instead of {earned.format()}. Your "
            "plan's documents say which applies."
        )
        assumptions.extend(age_notes)

    inputs = (
        "income.annual_salary",
        "employer_plan.contribution_percent",
        "employer_plan.match",
        f"rules/limits/{year}.yml",
    )
    if unclaimed.cents > 0:
        thresholds = snapshot.thresholds
        if unclaimed >= thresholds.money("tax_advantaged", "high_unclaimed_match"):
            severity = Severity.HIGH
        elif unclaimed >= thresholds.money("tax_advantaged", "medium_unclaimed_match"):
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW
        return attention(
            key,
            TITLE_MATCH,
            severity,
            f"You contribute {fmt_pct(rate)} of salary; the full match requires "
            f"{fmt_pct(plan.full_match_contribution)}. About {unclaimed.format()} a year of "
            "employer contributions goes unclaimed.",
            facts=facts,
            annual_impact=unclaimed,
            inputs=inputs,
            assumptions=assumptions,
        )
    return ok(
        key,
        TITLE_MATCH,
        f"Your {fmt_pct(rate)} contribution earns the full employer match, about "
        f"{full.format()} a year.",
        facts=facts,
        inputs=inputs,
        assumptions=assumptions,
    )


def _plan(snapshot: Snapshot, profile: Profile, limits: ContributionLimits) -> Observation | None:
    plan = profile.employer_plan
    if plan is None or plan.contribution_rate is None or profile.annual_salary is None:
        return None  # already reported by the match observation
    key = "F3.3.deferral"
    year = snapshot.as_of.year
    age, age_notes, age_missing = _age_context(profile, year)
    limit = limits.deferral_limit(age)
    elected = profile.annual_salary * plan.contribution_rate

    facts = [
        Fact("Elected contributions", f"{elected.format()}/yr ({fmt_pct(plan.contribution_rate)})"),
        Fact(f"{year} limit", limit.format()),
    ]
    if profile.birth_year is not None and age >= 50:
        catch_up = limits.catch_up_60_63 if 60 <= age <= 63 else limits.catch_up_50
        facts.append(Fact("Limit includes catch-up", f"{catch_up.format()} (age {age} in {year})"))
    assumptions = [
        "Elected contributions are salary × contribution rate for a full year; mid-year "
        "changes and amounts already contributed aren't tracked.",
        *age_notes,
    ]
    review = False
    if (
        profile.birth_year is not None
        and age >= 50
        and profile.annual_salary > limits.roth_catch_up_wage_threshold
    ):
        review = True
        assumptions.append(
            f"With wages above {limits.roth_catch_up_wage_threshold.format()}, catch-up "
            f"contributions in {year} must be designated Roth (SECURE 2.0). The test uses "
            "prior-year FICA wages; base salary is only a proxy for them here."
        )
    inputs = (
        "income.annual_salary",
        "employer_plan.contribution_percent",
        f"rules/limits/{year}.yml",
    )

    if elected > limit:
        excess = elected - limit
        return attention(
            key,
            TITLE_PLAN,
            Severity.LOW,
            f"At {fmt_pct(plan.contribution_rate)}, elected contributions of {elected.format()} "
            f"exceed the {year} limit of {limit.format()} by {excess.format()}; deferrals stop "
            "once the limit is reached.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
            missing=age_missing,
            professional_review=review,
        )
    room = limit - elected
    facts.append(Fact("Unused room", room.format()))
    return ok(
        key,
        TITLE_PLAN,
        f"Elected contributions of {elected.format()} leave {room.format()} of the "
        f"{limit.format()} {year} limit unused.",
        facts=facts,
        inputs=inputs,
        assumptions=assumptions,
        missing=age_missing,
        professional_review=review,
    )


def _ira(snapshot: Snapshot, profile: Profile, limits: ContributionLimits) -> Observation:
    key = "F3.3.ira"
    year = snapshot.as_of.year
    contributed = profile.ira_contributed
    if contributed is None:
        return insufficient(
            key,
            TITLE_IRA,
            "IRA contributions for this year aren't recorded.",
            ["Set ira.contributed_this_year in your profile (0 if none)."],
        )
    age, age_notes, age_missing = _age_context(profile, year)
    limit = limits.ira_limit(age)
    facts = [Fact("Contributed", contributed.format()), Fact(f"{year} limit", limit.format())]
    assumptions = [
        "The limit is shared across all your traditional and Roth IRAs.",
        "Roth IRA eligibility and traditional IRA deductibility both phase out with income; "
        "neither is assessed here.",
        f"Contributions for {year} can be made until the federal tax filing deadline "
        f"in {year + 1}.",
        *age_notes,
    ]
    inputs = ("ira.contributed_this_year", "household.birth_year", f"rules/limits/{year}.yml")
    if contributed > limit:
        excess = contributed - limit
        return attention(
            key,
            TITLE_IRA,
            Severity.MEDIUM,
            f"Recorded IRA contributions of {contributed.format()} exceed the {year} limit of "
            f"{limit.format()} by {excess.format()}. Excess contributions are subject to a 6% "
            "excise tax for each year they remain uncorrected.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
            missing=age_missing,
            professional_review=True,
        )
    room = limit - contributed
    facts.append(Fact("Unused room", room.format()))
    return ok(
        key,
        TITLE_IRA,
        f"{room.format()} of the {limit.format()} {year} IRA limit is unused.",
        facts=facts,
        inputs=inputs,
        assumptions=assumptions,
        missing=age_missing,
    )


def _hsa(snapshot: Snapshot, profile: Profile, limits: ContributionLimits) -> Observation:
    key = "F3.3.hsa"
    year = snapshot.as_of.year
    if not profile.hsa_stated:
        return insufficient(
            key,
            TITLE_HSA,
            "Your profile doesn't say whether you're enrolled in an HSA-eligible health plan.",
            ["Add an hsa section to your profile, or `hsa: null` if you aren't eligible."],
        )
    if profile.hsa is None:
        return not_applicable(key, TITLE_HSA, "Not enrolled in an HSA-eligible plan (hsa: null).")
    contributed = profile.hsa.contributed_this_year
    if contributed is None:
        return insufficient(
            key,
            TITLE_HSA,
            "HSA contributions for this year aren't recorded.",
            ["Set hsa.contributed_this_year in your profile, including employer contributions."],
        )

    age, age_notes, age_missing = _age_context(profile, year)
    limit = limits.hsa_limit(profile.hsa.coverage, age)
    facts = [
        Fact("Coverage", profile.hsa.coverage),
        Fact("Contributed", contributed.format()),
        Fact(f"{year} limit", limit.format()),
    ]
    assumptions = [
        f"Assumes HSA eligibility for all of {year}; eligibility for part of the year reduces "
        "the limit, subject to the last-month rule.",
        "Employer contributions count toward the limit.",
        *age_notes,
    ]
    if profile.birth_year is not None and age >= 55:
        assumptions.append(
            "The age-55 catch-up is included; it doesn't apply once enrolled in Medicare."
        )
    inputs = ("hsa.coverage", "hsa.contributed_this_year", f"rules/limits/{year}.yml")

    if contributed > limit:
        excess = contributed - limit
        return attention(
            key,
            TITLE_HSA,
            Severity.MEDIUM,
            f"Recorded HSA contributions of {contributed.format()} exceed the {year} limit of "
            f"{limit.format()} by {excess.format()}. Excess contributions are subject to a 6% "
            "excise tax unless withdrawn in time.",
            facts=facts,
            inputs=inputs,
            assumptions=assumptions,
            missing=age_missing,
            professional_review=True,
        )
    room = limit - contributed
    facts.append(Fact("Unused room", room.format()))
    return ok(
        key,
        TITLE_HSA,
        f"{room.format()} of the {limit.format()} {year} HSA limit is unused.",
        facts=facts,
        inputs=inputs,
        assumptions=assumptions,
        missing=age_missing,
    )
