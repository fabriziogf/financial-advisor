"""Reference rules the observation engine reads (PRD R3, §9.1).

Tracked in git, because none of them describes anyone's finances:

    rules/thresholds.yml        judgment-call cutoffs used by the checks
    rules/asset_classes.yml     the asset-class vocabulary
    rules/securities.yml        generic fund metadata (expense ratio, look-through)
    rules/limits/<year>.yml     published IRS contribution limits, one file per year

Not tracked: a local securities overlay in the data directory. The tracked catalog
holds a few common broad funds as reference. The funds you actually hold belong in
the overlay, because the *set* of symbols in a public file discloses your holdings
without a single share count appearing — the same reasoning that ruled out
per-institution import profiles (see importers/csv_import.py).

Every loader fails loudly on malformed input. A threshold that quietly fell back to a
default, or a limits file quietly borrowed from last year, produces results that are
confidently wrong in exactly the way R3 exists to prevent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .money import Money
from .paths import data_dir
from .yamlio import load_yaml

__all__ = [
    "RulesError",
    "LimitsNotFoundError",
    "Thresholds",
    "AssetClass",
    "SecurityInfo",
    "ContributionLimits",
    "Rules",
    "repo_root",
    "rules_dir",
    "local_securities_path",
    "normalize_symbol",
    "load_thresholds",
    "load_asset_classes",
    "load_securities",
    "load_limits",
    "load_rules",
]

TAX_EFFICIENCY = frozenset({"efficient", "inefficient", "neutral"})
LOCAL_SECURITIES_FILENAME = "securities.local.yml"
# Hand-typed look-through weights for a blended fund rarely sum to exactly 1.
# Within this tolerance they are normalized; beyond it they are an error.
_WEIGHT_TOLERANCE = Decimal("0.011")


class RulesError(Exception):
    """A rules file is missing or malformed."""


class LimitsNotFoundError(RulesError):
    """No contribution-limits file exists for the requested year."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def rules_dir() -> Path:
    override = os.environ.get("FA_RULES_DIR")
    return Path(override).expanduser().resolve() if override else repo_root() / "rules"


def local_securities_path() -> Path:
    return data_dir() / LOCAL_SECURITIES_FILENAME


def normalize_symbol(symbol: str) -> str:
    # Some brokerages mark core/sweep positions with trailing asterisks ("SPAXX**").
    return symbol.strip().upper().rstrip("*").strip()


def _load_mapping(path: Path) -> dict[Any, Any]:
    if not path.exists():
        raise RulesError(f"missing rules file: {path}")
    try:
        data = load_yaml(path)
    except Exception as exc:  # YAML errors carry line and column; surface them
        raise RulesError(f"{path.name}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RulesError(f"{path.name}: expected a mapping at the top level")
    return data


def _as_decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, bool):
        raise RulesError(f"{where}: expected a number, got {value!r}")
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise RulesError(f"{where}: could not read {value!r} as a number") from exc
    raise RulesError(f"{where}: expected a number, got {type(value).__name__}")


# --- Thresholds -----------------------------------------------------------
@dataclass(frozen=True)
class Thresholds:
    data: dict[str, dict[str, Any]]

    def _raw(self, section: str, key: str) -> Any:
        try:
            return self.data[section][key]
        except (KeyError, TypeError):
            raise RulesError(f"thresholds.yml: missing {section}.{key}") from None

    def decimal(self, section: str, key: str) -> Decimal:
        return _as_decimal(self._raw(section, key), f"thresholds.yml {section}.{key}")

    def integer(self, section: str, key: str) -> int:
        value = self.decimal(section, key)
        if value != value.to_integral_value():
            raise RulesError(f"thresholds.yml {section}.{key}: expected a whole number")
        return int(value)

    def money(self, section: str, key: str) -> Money:
        return Money(self.decimal(section, key))


def load_thresholds(directory: Path | None = None) -> Thresholds:
    data = _load_mapping((directory or rules_dir()) / "thresholds.yml")
    for section, body in data.items():
        if not isinstance(body, dict):
            raise RulesError(f"thresholds.yml: section {section!r} must be a mapping")
    return Thresholds(data=data)


# --- Asset classes --------------------------------------------------------
@dataclass(frozen=True)
class AssetClass:
    key: str
    label: str
    equity: bool
    tax_efficiency: str


def load_asset_classes(directory: Path | None = None) -> dict[str, AssetClass]:
    data = _load_mapping((directory or rules_dir()) / "asset_classes.yml")
    result: dict[str, AssetClass] = {}
    for key, body in data.items():
        where = f"asset_classes.yml {key}"
        if not isinstance(body, dict):
            raise RulesError(f"{where}: expected a mapping")
        label, equity, efficiency = body.get("label"), body.get("equity"), body.get("tax_efficiency")
        if not isinstance(label, str) or not label.strip():
            raise RulesError(f"{where}: label is required")
        if not isinstance(equity, bool):
            raise RulesError(f"{where}: equity must be true or false")
        if efficiency not in TAX_EFFICIENCY:
            raise RulesError(f"{where}: tax_efficiency must be one of {sorted(TAX_EFFICIENCY)}")
        result[str(key)] = AssetClass(str(key), label, equity, efficiency)
    if not result:
        raise RulesError("asset_classes.yml defines no asset classes")
    return result


# --- Securities -----------------------------------------------------------
@dataclass(frozen=True)
class SecurityInfo:
    symbol: str
    name: str | None
    expense_ratio: Decimal | None
    # Asset-class look-through weights summing to 1. Empty = unclassified.
    weights: dict[str, Decimal]
    category: str | None
    # None means not stated. Concentration analysis reports such holdings as
    # unassessed rather than assuming either way: calling a total-market fund a
    # concentrated position is noise, and calling a single stock diversified hides risk.
    diversified: bool | None
    source: str  # "tracked" | "local"

    @property
    def is_classified(self) -> bool:
        return bool(self.weights)


_SECURITY_FIELDS = frozenset({"name", "expense_ratio", "asset_class", "category", "diversified"})


def _parse_security(
    symbol: str, body: Any, *, source: str, filename: str, asset_classes: dict[str, AssetClass]
) -> SecurityInfo:
    where = f"{filename} {symbol}"
    if not isinstance(body, dict):
        raise RulesError(f"{where}: expected a mapping")
    unknown = set(body) - _SECURITY_FIELDS
    if unknown:
        raise RulesError(
            f"{where}: unknown field(s) {sorted(unknown)}. Allowed: {sorted(_SECURITY_FIELDS)}. "
            "Holdings data (quantities, values, accounts) never belongs in a securities file."
        )

    expense_ratio = None
    if body.get("expense_ratio") is not None:
        expense_ratio = _as_decimal(body["expense_ratio"], f"{where} expense_ratio")
        if not Decimal(0) <= expense_ratio < Decimal("0.05"):
            raise RulesError(
                f"{where}: expense_ratio {expense_ratio} is outside 0–5%. "
                "Write it as a fraction: 0.0003 for 0.03%."
            )

    weights: dict[str, Decimal] = {}
    raw_weights = body.get("asset_class")
    if raw_weights is not None:
        if not isinstance(raw_weights, dict) or not raw_weights:
            raise RulesError(f"{where}: asset_class must map asset classes to weights")
        for cls, raw in raw_weights.items():
            if cls not in asset_classes:
                raise RulesError(
                    f"{where}: unknown asset class {cls!r}. Known: {sorted(asset_classes)}"
                )
            weight = _as_decimal(raw, f"{where} asset_class.{cls}")
            if not Decimal(0) <= weight <= 1:
                raise RulesError(f"{where}: asset_class.{cls} must be between 0 and 1")
            weights[str(cls)] = weight
        total = sum(weights.values(), Decimal(0))
        if abs(total - 1) > _WEIGHT_TOLERANCE:
            raise RulesError(f"{where}: asset_class weights sum to {total}, expected 1.0")
        if total != 1:
            weights = {cls: w / total for cls, w in weights.items()}

    diversified = body.get("diversified")
    if diversified is not None and not isinstance(diversified, bool):
        raise RulesError(f"{where}: diversified must be true or false")
    category = body.get("category")
    if category is not None and not isinstance(category, str):
        raise RulesError(f"{where}: category must be text")
    name = body.get("name")

    return SecurityInfo(
        symbol=symbol,
        name=str(name) if name else None,
        expense_ratio=expense_ratio,
        weights=weights,
        category=category,
        diversified=diversified,
        source=source,
    )


def _parse_catalog(
    data: dict[Any, Any], *, source: str, filename: str, asset_classes: dict[str, AssetClass]
) -> dict[str, SecurityInfo]:
    catalog: dict[str, SecurityInfo] = {}
    for raw_symbol, body in data.items():
        if isinstance(raw_symbol, bool):
            # YAML 1.1 reads bare ON, OFF, YES, NO as booleans — all real tickers.
            raise RulesError(
                f"{filename}: a symbol was read as the boolean {raw_symbol}. "
                "Quote tickers like ON, YES, or NO: \"ON\":"
            )
        symbol = normalize_symbol(str(raw_symbol))
        catalog[symbol] = _parse_security(
            symbol, body, source=source, filename=filename, asset_classes=asset_classes
        )
    return catalog


def load_securities(
    asset_classes: dict[str, AssetClass],
    directory: Path | None = None,
    local_path: Path | None = None,
) -> dict[str, SecurityInfo]:
    """Tracked catalog, then the local overlay on top. Local entries replace whole."""
    tracked = _load_mapping((directory or rules_dir()) / "securities.yml")
    catalog = _parse_catalog(
        tracked, source="tracked", filename="securities.yml", asset_classes=asset_classes
    )
    local = local_path or local_securities_path()
    if local.exists():
        catalog.update(
            _parse_catalog(
                _load_mapping(local),
                source="local",
                filename=LOCAL_SECURITIES_FILENAME,
                asset_classes=asset_classes,
            )
        )
    return catalog


# --- Contribution limits --------------------------------------------------
@dataclass(frozen=True)
class ContributionLimits:
    year: int
    source: str
    elective_deferral: Money
    catch_up_50: Money
    catch_up_60_63: Money
    compensation_limit: Money
    roth_catch_up_wage_threshold: Money
    ira: Money
    ira_catch_up_50: Money
    hsa_self: Money
    hsa_family: Money
    hsa_catch_up_55: Money

    def deferral_limit(self, age: int) -> Money:
        """402(g) limit plus catch-up. `age` is the age attained by 31 December.

        SECURE 2.0's higher catch-up applies only at 60–63; at 64 it reverts to the
        standard 50+ amount. Treating it as "60 and over" overstates room from 64 on.
        """
        if 60 <= age <= 63:
            return self.elective_deferral + self.catch_up_60_63
        if age >= 50:
            return self.elective_deferral + self.catch_up_50
        return self.elective_deferral

    def ira_limit(self, age: int) -> Money:
        return self.ira + (self.ira_catch_up_50 if age >= 50 else Money(0))

    def hsa_limit(self, coverage: str, age: int) -> Money:
        base = self.hsa_family if coverage == "family" else self.hsa_self
        return base + (self.hsa_catch_up_55 if age >= 55 else Money(0))


_LIMIT_FIELDS = (
    "elective_deferral",
    "catch_up_50",
    "catch_up_60_63",
    "compensation_limit",
    "roth_catch_up_wage_threshold",
    "ira",
    "ira_catch_up_50",
    "hsa_self",
    "hsa_family",
    "hsa_catch_up_55",
)


def load_limits(year: int, directory: Path | None = None) -> ContributionLimits:
    path = (directory or rules_dir()) / "limits" / f"{year}.yml"
    if not path.exists():
        raise LimitsNotFoundError(
            f"No contribution limits on file for {year} (expected rules/limits/{year}.yml). "
            "Limits are published annually; borrowing another year's figures would produce "
            "confidently wrong results (PRD R3)."
        )
    data = _load_mapping(path)
    if data.get("year") != year:
        raise RulesError(f"{path.name}: declares year {data.get('year')!r}, expected {year}")
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise RulesError(f"{path.name}: `source` is required — cite where the figures came from")
    unknown = set(data) - set(_LIMIT_FIELDS) - {"year", "source", "notes"}
    if unknown:
        raise RulesError(f"{path.name}: unknown field(s) {sorted(unknown)}")

    values: dict[str, Money] = {}
    for name in _LIMIT_FIELDS:
        if name not in data:
            raise RulesError(f"{path.name}: missing {name}")
        amount = _as_decimal(data[name], f"{path.name} {name}")
        if amount < 0:
            raise RulesError(f"{path.name}: {name} cannot be negative")
        values[name] = Money(amount)
    return ContributionLimits(year=year, source=source.strip(), **values)


# --- Bundle ---------------------------------------------------------------
@dataclass(frozen=True)
class Rules:
    thresholds: Thresholds
    asset_classes: dict[str, AssetClass]
    securities: dict[str, SecurityInfo]
    limits: ContributionLimits | None
    # Set when no limits file exists for the year. Checks that need limits report
    # INSUFFICIENT_DATA with this message. A *malformed* limits file still raises.
    limits_error: str | None = None


def load_rules(year: int) -> Rules:
    asset_classes = load_asset_classes()
    try:
        limits, limits_error = load_limits(year), None
    except LimitsNotFoundError as exc:
        limits, limits_error = None, str(exc)
    return Rules(
        thresholds=load_thresholds(),
        asset_classes=asset_classes,
        securities=load_securities(asset_classes),
        limits=limits,
        limits_error=limits_error,
    )
