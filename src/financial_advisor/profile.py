"""Your financial profile (PRD F1.7) — the inputs no export contains.

Employer match formula, contribution rate, HSA enrollment, target allocation,
insurance coverage. The checks that change advice the most depend on these, and
none of them can be imported. So they are hand-written in a YAML file that lives
in the data directory, outside the repository (see paths.py for why).

Validation is strict on purpose:

* **Unknown keys are errors.** A typo like `emergency_funds:` would otherwise be
  ignored, the section would read as "not stated", and the report would ask for
  something you believe you already gave it.
* **Every problem is reported at once**, with its path, rather than one per run.
* **"Not stated" and "none" are different.** A missing `employer_plan:` means you
  haven't said; `employer_plan: null` means you have no plan. The first yields
  "insufficient data", the second "not applicable". Collapsing them would make a
  forgotten section look like a deliberate answer.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .money import Money, MoneyError
from .paths import data_dir, ensure_data_dir
from .rules import normalize_symbol, repo_root
from .yamlio import load_yaml

__all__ = [
    "Profile",
    "EmployerPlan",
    "MatchTier",
    "HSAEnrollment",
    "ProfileError",
    "profile_path",
    "example_profile_path",
    "load_profile",
    "parse_profile",
    "init_profile",
]

PROFILE_FILENAME = "profile.yml"
_ABSENT = object()


class ProfileError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        listing = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"profile.yml has {len(problems)} problem(s):\n{listing}")


def profile_path() -> Path:
    return data_dir() / PROFILE_FILENAME


def example_profile_path() -> Path:
    return repo_root() / "config" / "profile.example.yml"


# --- Model ----------------------------------------------------------------
@dataclass(frozen=True)
class MatchTier:
    rate: Decimal    # fraction: 1.0 means the employer adds 100% of the matched amount
    up_to: Decimal   # cumulative bound, as a fraction of salary


@dataclass(frozen=True)
class EmployerPlan:
    contribution_rate: Decimal | None   # fraction of salary
    match: tuple[MatchTier, ...]
    employer_stock_symbol: str | None

    def max_match_rate(self) -> Decimal:
        """Employer contribution, as a fraction of salary, at full participation."""
        total, floor = Decimal(0), Decimal(0)
        for tier in self.match:
            total += tier.rate * (tier.up_to - floor)
            floor = tier.up_to
        return total

    def match_rate_at(self, contribution: Decimal) -> Decimal:
        """Employer contribution, as a fraction of salary, at a given deferral rate."""
        total, floor = Decimal(0), Decimal(0)
        for tier in self.match:
            matched = min(max(contribution - floor, Decimal(0)), tier.up_to - floor)
            total += tier.rate * matched
            floor = tier.up_to
        return total

    @property
    def full_match_contribution(self) -> Decimal:
        """The deferral rate needed to receive the entire match."""
        return self.match[-1].up_to if self.match else Decimal(0)


@dataclass(frozen=True)
class HSAEnrollment:
    coverage: str                        # "self" | "family"
    contributed_this_year: Money | None


@dataclass(frozen=True)
class Profile:
    reviewed_on: date | None = None
    birth_year: int | None = None
    dependents: int | None = None
    annual_salary: Money | None = None
    income_stability: str | None = None
    employer_plan_stated: bool = False
    employer_plan: EmployerPlan | None = None
    ira_contributed: Money | None = None
    hsa_stated: bool = False
    hsa: HSAEnrollment | None = None
    emergency_target_months: Decimal | None = None
    monthly_essential_expenses: Money | None = None
    target_allocation: dict[str, Decimal] | None = None   # fractions summing to 1
    drift_tolerance: Decimal | None = None
    insurance_stated: bool = False
    life_coverage: Money | None = None
    long_term_disability: bool | None = None
    umbrella_coverage: Money | None = None

    def age_in(self, year: int) -> int | None:
        """Age attained by 31 December of `year` — the age IRS catch-up rules use."""
        return None if self.birth_year is None else year - self.birth_year


# --- Parsing --------------------------------------------------------------
_SECTIONS: dict[str, frozenset[str]] = {
    "household": frozenset({"birth_year", "dependents"}),
    "income": frozenset({"annual_salary", "stability"}),
    "employer_plan": frozenset({"contribution_percent", "match", "employer_stock_symbol"}),
    "ira": frozenset({"contributed_this_year"}),
    "hsa": frozenset({"coverage", "contributed_this_year"}),
    "emergency_fund": frozenset({"target_months", "monthly_essential_expenses"}),
    "investments": frozenset({"target_allocation_percent", "drift_tolerance_percent"}),
    "insurance": frozenset({"life_coverage", "long_term_disability", "umbrella_coverage"}),
}
_TOP_LEVEL = frozenset(_SECTIONS) | {"reviewed_on"}
# Sections where an explicit null is a meaningful answer ("I have none").
_NULLABLE_SECTIONS = frozenset({"employer_plan", "hsa"})


class _Reader:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def fail(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def decimal(self, value: Any, where: str) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool):
            self.fail(where, f"expected a number, got {value!r}")
            return None
        if isinstance(value, (int, Decimal)):
            return Decimal(value)
        if isinstance(value, str):
            try:
                return Decimal(value.strip().rstrip("%").replace(",", ""))
            except InvalidOperation:
                pass
        self.fail(where, f"expected a number, got {value!r}")
        return None

    def bounded(self, value: Any, where: str, low: Decimal, high: Decimal) -> Decimal | None:
        number = self.decimal(value, where)
        if number is not None and not low <= number <= high:
            self.fail(where, f"must be between {low} and {high}, got {number}")
            return None
        return number

    def percent(self, value: Any, where: str, high: Decimal = Decimal(100)) -> Decimal | None:
        number = self.bounded(value, where, Decimal(0), high)
        return None if number is None else number / 100

    def integer(self, value: Any, where: str, low: int, high: int) -> int | None:
        number = self.decimal(value, where)
        if number is None:
            return None
        if number != number.to_integral_value() or not low <= number <= high:
            self.fail(where, f"must be a whole number from {low} to {high}, got {number}")
            return None
        return int(number)

    def money(self, value: Any, where: str) -> Money | None:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                amount = Money.parse(value)
            elif isinstance(value, (int, Decimal)) and not isinstance(value, bool):
                amount = Money(value)
            else:
                raise MoneyError(f"expected an amount, got {value!r}")
        except (MoneyError, TypeError) as exc:
            self.fail(where, str(exc))
            return None
        if amount.is_negative():
            self.fail(where, "cannot be negative")
            return None
        return amount

    def choice(self, value: Any, where: str, options: tuple[str, ...]) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value.strip().lower() not in options:
            self.fail(where, f"must be one of {', '.join(options)}, got {value!r}")
            return None
        return value.strip().lower()

    def boolean(self, value: Any, where: str) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            self.fail(where, f"must be true or false, got {value!r}")
            return None
        return value

    def date_value(self, value: Any, where: str) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                pass
        self.fail(where, f"expected a date like 2026-01-31, got {value!r}")
        return None


def _section(data: dict[str, Any], name: str, reader: _Reader) -> Any:
    """The section mapping; None for explicit null; _ABSENT if not present."""
    if name not in data:
        return _ABSENT
    body = data[name]
    if body is None:
        return None
    if not isinstance(body, dict):
        reader.fail(name, "expected a section with fields")
        return _ABSENT
    unknown = set(body) - _SECTIONS[name]
    for key in sorted(unknown):
        reader.fail(f"{name}.{key}", f"unknown field. Expected one of: {', '.join(sorted(_SECTIONS[name]))}")
    return body


def _parse_match(raw: Any, reader: _Reader) -> tuple[MatchTier, ...]:
    where = "employer_plan.match"
    if raw is None:
        return ()
    if not isinstance(raw, list):
        reader.fail(where, "expected a list of tiers")
        return ()
    tiers: list[MatchTier] = []
    floor = Decimal(0)
    for index, item in enumerate(raw):
        at = f"{where}[{index}]"
        if not isinstance(item, dict):
            reader.fail(at, "expected rate_percent and up_to_percent")
            continue
        unknown = set(item) - {"rate_percent", "up_to_percent"}
        for key in sorted(unknown):
            reader.fail(f"{at}.{key}", "unknown field. Expected rate_percent, up_to_percent")
        rate = reader.percent(item.get("rate_percent"), f"{at}.rate_percent", high=Decimal(500))
        up_to = reader.percent(item.get("up_to_percent"), f"{at}.up_to_percent")
        if rate is None or up_to is None:
            if "rate_percent" not in item or "up_to_percent" not in item:
                reader.fail(at, "both rate_percent and up_to_percent are required")
            continue
        if up_to <= floor:
            reader.fail(
                f"{at}.up_to_percent",
                "bounds are cumulative and must increase: "
                "'50% of the next 2%' after a 3% tier is written up_to_percent: 5",
            )
            continue
        tiers.append(MatchTier(rate=rate, up_to=up_to))
        floor = up_to
    return tuple(tiers)


def parse_profile(data: Any, *, asset_classes: set[str], today: date) -> Profile:
    reader = _Reader()
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ProfileError(["the file must contain a mapping of sections"])
    for key in sorted(set(data) - _TOP_LEVEL):
        reader.fail(str(key), f"unknown section. Expected one of: {', '.join(sorted(_TOP_LEVEL))}")

    fields: dict[str, Any] = {"reviewed_on": reader.date_value(data.get("reviewed_on"), "reviewed_on")}

    household = _section(data, "household", reader)
    if isinstance(household, dict):
        fields["birth_year"] = reader.integer(
            household.get("birth_year"), "household.birth_year", 1900, today.year
        )
        fields["dependents"] = reader.integer(household.get("dependents"), "household.dependents", 0, 30)

    income = _section(data, "income", reader)
    if isinstance(income, dict):
        fields["annual_salary"] = reader.money(income.get("annual_salary"), "income.annual_salary")
        fields["income_stability"] = reader.choice(
            income.get("stability"), "income.stability", ("stable", "variable")
        )

    plan = _section(data, "employer_plan", reader)
    if plan is not _ABSENT:
        fields["employer_plan_stated"] = True
        if isinstance(plan, dict):
            symbol = plan.get("employer_stock_symbol")
            if symbol is not None and not isinstance(symbol, str):
                reader.fail("employer_plan.employer_stock_symbol", "must be a ticker symbol")
                symbol = None
            fields["employer_plan"] = EmployerPlan(
                contribution_rate=reader.percent(
                    plan.get("contribution_percent"), "employer_plan.contribution_percent"
                ),
                match=_parse_match(plan.get("match"), reader),
                employer_stock_symbol=normalize_symbol(symbol) if symbol else None,
            )

    ira = _section(data, "ira", reader)
    if isinstance(ira, dict):
        fields["ira_contributed"] = reader.money(ira.get("contributed_this_year"), "ira.contributed_this_year")

    hsa = _section(data, "hsa", reader)
    if hsa is not _ABSENT:
        fields["hsa_stated"] = True
        if isinstance(hsa, dict):
            coverage = reader.choice(hsa.get("coverage"), "hsa.coverage", ("self", "family"))
            if coverage is None and hsa.get("coverage") is None:
                reader.fail("hsa.coverage", "required when the hsa section is present (self or family)")
            if coverage:
                fields["hsa"] = HSAEnrollment(
                    coverage=coverage,
                    contributed_this_year=reader.money(
                        hsa.get("contributed_this_year"), "hsa.contributed_this_year"
                    ),
                )

    emergency = _section(data, "emergency_fund", reader)
    if isinstance(emergency, dict):
        fields["emergency_target_months"] = reader.bounded(
            emergency.get("target_months"), "emergency_fund.target_months", Decimal("0.5"), Decimal(36)
        )
        fields["monthly_essential_expenses"] = reader.money(
            emergency.get("monthly_essential_expenses"), "emergency_fund.monthly_essential_expenses"
        )

    investments = _section(data, "investments", reader)
    if isinstance(investments, dict):
        fields["drift_tolerance"] = reader.percent(
            investments.get("drift_tolerance_percent"),
            "investments.drift_tolerance_percent",
            high=Decimal(50),
        )
        raw_target = investments.get("target_allocation_percent")
        if raw_target is not None:
            where = "investments.target_allocation_percent"
            if not isinstance(raw_target, dict) or not raw_target:
                reader.fail(where, "expected asset classes mapped to percentages")
            else:
                target: dict[str, Decimal] = {}
                for cls, raw in raw_target.items():
                    if cls not in asset_classes:
                        reader.fail(f"{where}.{cls}", f"unknown asset class. Known: {', '.join(sorted(asset_classes))}")
                        continue
                    share = reader.percent(raw, f"{where}.{cls}")
                    if share is not None:
                        target[str(cls)] = share
                total = sum(target.values(), Decimal(0))
                if target and abs(total - 1) > Decimal("0.0001"):
                    reader.fail(where, f"must sum to 100, got {total * 100}")
                elif target:
                    fields["target_allocation"] = target

    insurance = _section(data, "insurance", reader)
    if isinstance(insurance, dict):
        fields["insurance_stated"] = True
        fields["life_coverage"] = reader.money(insurance.get("life_coverage"), "insurance.life_coverage")
        fields["long_term_disability"] = reader.boolean(
            insurance.get("long_term_disability"), "insurance.long_term_disability"
        )
        fields["umbrella_coverage"] = reader.money(
            insurance.get("umbrella_coverage"), "insurance.umbrella_coverage"
        )

    if reader.problems:
        raise ProfileError(reader.problems)
    return Profile(**fields)


def load_profile(
    path: Path | None = None, *, asset_classes: set[str], today: date | None = None
) -> Profile | None:
    """The profile, or None if no file exists yet. Malformed files raise ProfileError."""
    target = path or profile_path()
    if not target.exists():
        return None
    try:
        data = load_yaml(target)
    except Exception as exc:
        raise ProfileError([f"could not read YAML: {exc}"]) from exc
    return parse_profile(data, asset_classes=asset_classes, today=today or date.today())


def init_profile(*, force: bool = False) -> Path:
    """Copy the example profile into the data directory, owner-only."""
    destination = profile_path()
    if destination.exists() and not force:
        raise FileExistsError(f"a profile already exists at {destination}")
    ensure_data_dir()
    shutil.copyfile(example_profile_path(), destination)
    destination.chmod(0o600)
    return destination
