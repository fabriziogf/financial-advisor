"""`fa` — command line for the advisor.

Advisory only (PRD §4/G5). Nothing here can move money; there is no institution
write path anywhere in the codebase for a command to call.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NoReturn

import click

from .analysis.engine import CHECKS, run_checks
from .analysis.model import Status, fmt_rate
from .analysis.portfolio import default_tax_treatment
from .analysis.snapshot import build_snapshot
from .db.connection import (
    SCHEMA_VERSION,
    SchemaVersionError,
    connect,
    initialize,
    is_initialized,
    migrate,
)
from .db.store import (
    create_account,
    find_account,
    get_terms,
    list_accounts,
    set_balance,
    set_terms,
)
from .importers.csv_import import ImportError_, SignConvention, import_file
from .importers.holdings_import import import_holdings
from .money import Money, MoneyError
from .paths import data_dir, database_path
from .profile import (
    ProfileError,
    example_profile_path,
    init_profile,
    load_profile,
    profile_path,
)
from .rates import SERIES_LABEL, RatesError, fetch_benchmark, load_benchmark, save_benchmark
from .reports.net_worth import compute_net_worth, format_net_worth
from .reports.observations import format_observations
from .rules import RulesError, load_rules, local_securities_path


def _fail(message: str) -> NoReturn:
    raise click.ClickException(message)


def _date(text: str | None, option: str = "--as-of") -> date:
    if not text:
        return date.today()
    try:
        return date.fromisoformat(text)
    except ValueError:
        _fail(f"{option}: expected a date like 2026-09-30, got {text!r}")


def _percent(text: str, option: str) -> Decimal:
    """'4.25' or '4.25%' -> Decimal('0.0425')."""
    try:
        value = Decimal(text.strip().rstrip("%"))
    except InvalidOperation:
        _fail(f"{option}: expected a percentage like 4.25, got {text!r}")
    if not Decimal(0) <= value <= Decimal(100):
        _fail(f"{option}: must be between 0 and 100, got {value}")
    return value / 100


class _Group(click.Group):
    """Turns the system's expected, user-fixable errors into clean CLI messages."""

    def invoke(self, ctx: click.Context):  # type: ignore[override]
        try:
            return super().invoke(ctx)
        except (SchemaVersionError, ProfileError, RulesError, RatesError) as exc:
            raise click.ClickException(str(exc)) from exc
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_Group)
@click.version_option(package_name="financial-advisor")
def cli() -> None:
    """Read-only personal financial advisor."""


@cli.command()
def init() -> None:
    """Create the local database, or upgrade an existing one."""
    if is_initialized():
        result = migrate()
        if not result.applied:
            click.echo(f"Already initialized (schema v{result.from_version}): {database_path()}")
            return
        click.echo(f"✓ Upgraded schema v{result.from_version} → v{SCHEMA_VERSION}")
        if result.backup:
            click.echo(f"  Backup of the previous version: {result.backup}")
        return
    path = initialize()
    click.echo(f"✓ Database created: {path}")
    click.echo(f"  Data directory:   {data_dir()}")
    click.echo()
    click.echo("This lives outside the repository on purpose — the working tree is a")
    click.echo("public git repo inside an iCloud-synced folder. See SECURITY.md.")


@cli.command("accounts")
def accounts_cmd() -> None:
    """List accounts."""
    with connect() as conn:
        rows = list_accounts(conn)
        if not rows:
            click.echo("No accounts yet. Add one with `fa account-add`.")
            return
        for account in rows:
            side = "liability" if account.is_liability else "asset"
            where = f"{account.institution} · " if account.institution else ""
            click.echo(f"  {where}{account.name}  [{account.type_label}, {side}]")


@cli.command("account-add")
@click.option("--name", required=True, help="Your label for the account.")
@click.option("--type", "type_code", required=True, help="Account type code.")
@click.option("--institution", default=None, help="Institution name.")
@click.option(
    "--tax",
    "tax_treatment",
    type=click.Choice(["taxable", "tax_deferred", "tax_free"]),
    default=None,
    help="Defaults from the account type: 401(k) and traditional IRA are tax_deferred; "
    "Roth IRA, HSA, and 529 are tax_free; everything else is taxable.",
)
@click.option("--manual", is_flag=True, help="Declared asset with hand-entered values.")
def account_add(name, type_code, institution, tax_treatment, manual) -> None:
    """Add an account. Use --manual for property and other declared assets."""
    tax_treatment = tax_treatment or default_tax_treatment(type_code)
    with connect() as conn:
        if find_account(conn, name):
            _fail(f"An account named {name!r} already exists.")
        try:
            create_account(
                conn,
                name=name,
                type_code=type_code,
                institution=institution,
                tax_treatment=tax_treatment,
                is_manual=manual,
            )
        except ValueError as exc:
            _fail(str(exc))
    click.echo(f"✓ Added {name}")


@cli.command("account-types")
def account_types() -> None:
    """List valid account type codes."""
    with connect() as conn:
        for row in conn.execute(
            "SELECT code, label, is_liability FROM account_type ORDER BY sort_order"
        ):
            side = "liability" if row["is_liability"] else "asset"
            click.echo(f"  {row['code']:<18} {row['label']:<24} ({side})")


@cli.command("balance")
@click.option("--account", "account_name", required=True)
@click.option("--amount", required=True, help="e.g. 1234.56 or -500.00")
@click.option("--as-of", "as_of", default=None, help="ISO date. Defaults to today.")
def balance_cmd(account_name, amount, as_of) -> None:
    """Record a balance for an account."""
    when = date.fromisoformat(as_of) if as_of else date.today()
    try:
        value = Money.parse(amount)
    except MoneyError as exc:
        _fail(str(exc))
    with connect() as conn:
        account = find_account(conn, account_name)
        if not account:
            _fail(f"No account named {account_name!r}.")
        set_balance(conn, account.id, value, when, source="manual")
    click.echo(f"✓ {account_name}: {value.format()} as of {when}")


@cli.command("import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--account", "account_name", required=True)
@click.option(
    "--sign",
    type=click.Choice([s.value for s in SignConvention]),
    default=SignConvention.NATURAL.value,
    help="'flipped' if the export reports purchases as positive (common for cards).",
)
def import_cmd(csv_path: Path, account_name: str, sign: str) -> None:
    """Import a transaction CSV export."""
    with connect() as conn:
        account = find_account(conn, account_name)
        if not account:
            _fail(f"No account named {account_name!r}. Add it with `fa account-add`.")
        try:
            result = import_file(
                conn,
                csv_path,
                account_id=account.id,
                account_name=account.name,
                is_liability=account.is_liability,
                sign=SignConvention(sign),
            )
        except ImportError_ as exc:
            _fail(str(exc))

    if result.skipped_file:
        click.echo(f"Already imported {csv_path.name} — nothing to do.")
        return

    click.echo(f"✓ {csv_path.name} → {result.account}")
    click.echo(f"    read       {result.rows_read}")
    click.echo(f"    inserted   {result.inserted}")
    click.echo(f"    duplicates {result.duplicates}")
    if result.date_range:
        click.echo(f"    range      {result.date_range[0]} … {result.date_range[1]}")
    for warning in result.warnings:
        click.echo(f"  ⚠ {warning}")


@cli.command("networth")
@click.option("--as-of", "as_of", default=None, help="ISO date. Defaults to today.")
def networth_cmd(as_of) -> None:
    """Show the net worth statement."""
    when = date.fromisoformat(as_of) if as_of else date.today()
    with connect() as conn:
        report = compute_net_worth(conn, when)
    click.echo(format_net_worth(report))


# --- M1: inputs the observation engine needs ------------------------------
@cli.command("terms")
@click.option("--account", "account_name", required=True)
@click.option(
    "--rate",
    default=None,
    help="Annual rate as a percent: APY for deposit accounts, APR for debts. 4.25 means 4.25%.",
)
@click.option("--kind", type=click.Choice(["fixed", "variable"]), default=None)
@click.option("--min-payment", default=None, help="Minimum monthly payment, e.g. 250.00.")
@click.option("--promo-ends", default=None, help="Date a promotional rate ends, e.g. 2026-12-31.")
@click.option("--post-promo-rate", default=None, help="Rate after the promotion, as a percent.")
@click.option(
    "--revolving/--pays-in-full",
    default=None,
    help="Credit cards: whether the balance carries over from month to month.",
)
def terms_cmd(
    account_name, rate, kind, min_payment, promo_ends, post_promo_rate, revolving
) -> None:
    """Record an account's interest rate and terms. Only the options you pass change.

    With no options, shows what's recorded.
    """
    changes: dict[str, object] = {}
    if rate is not None:
        changes["rate"] = _percent(rate, "--rate")
    if kind is not None:
        changes["rate_kind"] = kind
    if min_payment is not None:
        try:
            changes["minimum_payment"] = abs(Money.parse(min_payment))
        except MoneyError as exc:
            _fail(f"--min-payment: {exc}")
    if promo_ends is not None:
        changes["promo_ends_on"] = _date(promo_ends, "--promo-ends")
    if post_promo_rate is not None:
        changes["post_promo_rate"] = _percent(post_promo_rate, "--post-promo-rate")
    if revolving is not None:
        changes["revolving"] = revolving

    with connect() as conn:
        account = find_account(conn, account_name)
        if not account:
            _fail(f"No account named {account_name!r}.")
        if changes:
            try:
                terms = set_terms(conn, account.id, updated_on=date.today(), **changes)
            except (ValueError, TypeError) as exc:
                _fail(str(exc))
        else:
            terms = get_terms(conn).get(account.id)
            if terms is None:
                click.echo(f"No terms recorded for {account_name}.")
                return

    parts = []
    if terms.rate is not None:
        kind_text = f" {terms.rate_kind}" if terms.rate_kind else ""
        parts.append(f"rate {fmt_rate(terms.rate)}{kind_text}")
    if terms.minimum_payment is not None:
        parts.append(f"minimum {terms.minimum_payment.format()}")
    if terms.promo_ends_on:
        after = f" → {fmt_rate(terms.post_promo_rate)}" if terms.post_promo_rate is not None else ""
        parts.append(f"promo ends {terms.promo_ends_on}{after}")
    if terms.revolving is not None:
        parts.append("revolving" if terms.revolving else "paid in full monthly")
    prefix = "✓ " if changes else ""
    click.echo(f"{prefix}{account_name}: {', '.join(parts) or 'no terms'}")


@cli.command("holdings")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--account", "account_name", required=True)
@click.option(
    "--as-of", "as_of", default=None, help="Date the holdings are as of. Defaults to today."
)
@click.option(
    "--only", default=None, help="Multi-account files: text matched in the account column."
)
@click.option("--no-balance", is_flag=True, help="Don't set the balance from the holdings total.")
def holdings_cmd(csv_path: Path, account_name: str, as_of, only, no_balance) -> None:
    """Import a holdings (positions) export for an investment account."""
    when = _date(as_of)
    with connect() as conn:
        account = find_account(conn, account_name)
        if not account:
            _fail(f"No account named {account_name!r}. Add it with `fa account-add`.")
        if not account.is_investment:
            _fail(
                f"{account_name} is a {account.type_label} account; holdings apply to investment "
                "accounts (see `fa account-types`)."
            )
        try:
            result = import_holdings(
                conn,
                csv_path,
                account_id=account.id,
                account_name=account.name,
                as_of=when,
                only=only,
                update_balance=not no_balance,
            )
        except ImportError_ as exc:
            _fail(str(exc))

    if result.skipped_file:
        click.echo(f"Already imported {csv_path.name} for {account_name} — nothing to do.")
        return

    total = result.total.format() if result.total is not None else "unknown"
    click.echo(f"✓ {csv_path.name} → {account_name}: {result.positions_written} positions, {total}")
    balance_note = "; balance updated to match" if result.balance_updated else ""
    click.echo(f"    as of {when}{balance_note}")
    for row in result.skipped_rows:
        click.echo(f"  ⚠ {row}")

    catalog = load_rules(when.year).securities
    unknown = sorted({h.symbol for h in result.holdings} - set(catalog))
    if unknown:
        click.echo(f"  ⚠ Not in your securities catalog: {', '.join(unknown)}")
        click.echo(f"    Describe them in {local_securities_path()}")
        click.echo("    (same format as rules/securities.yml). Until then they're unclassified.")


@cli.group("rates")
def rates_group() -> None:
    """The benchmark interest rate used by the cash-drag check."""


@rates_group.command("refresh")
def rates_refresh() -> None:
    """Fetch the 3-month Treasury bill rate from FRED.

    One request to fred.stlouisfed.org for a public series. Nothing about you is sent.
    """
    rate = fetch_benchmark(today=date.today())
    save_benchmark(rate)
    click.echo(f"✓ {SERIES_LABEL}: {fmt_rate(rate.rate)} (observed {rate.observed_on})")


@rates_group.command("show")
def rates_show() -> None:
    """Show the cached benchmark rate."""
    rate = load_benchmark()
    if rate is None:
        click.echo("No benchmark rate cached. Run `fa rates refresh`.")
        return
    click.echo(
        f"{SERIES_LABEL}: {fmt_rate(rate.rate)} (observed {rate.observed_on}, "
        f"fetched {rate.fetched_on})"
    )


@cli.group("profile")
def profile_group() -> None:
    """Your financial profile: the inputs no export contains."""


@profile_group.command("init")
@click.option("--force", is_flag=True, help="Replace an existing profile with the example.")
def profile_init(force: bool) -> None:
    """Create your profile from the example, outside the repository."""
    try:
        path = init_profile(force=force)
    except FileExistsError as exc:
        _fail(f"{exc}. Edit it directly, or pass --force to replace it with the example.")
    click.echo(f"✓ Profile created: {path}")
    click.echo("  It contains FICTIONAL example figures — replace them with your own.")
    click.echo("  Owner-only permissions, outside the repo. Validate with `fa profile check`.")


@profile_group.command("path")
def profile_path_cmd() -> None:
    """Print where your profile lives."""
    click.echo(profile_path())


@profile_group.command("check")
def profile_check() -> None:
    """Validate your profile and report what's unstated."""
    today = date.today()
    rules = load_rules(today.year)
    profile = load_profile(asset_classes=set(rules.asset_classes), today=today)
    if profile is None:
        _fail(f"No profile at {profile_path()}. Create one with `fa profile init`.")
    click.echo(f"✓ Profile is valid: {profile_path()}")
    if _profile_is_example():
        click.echo("  ⚠ It is still the unmodified example, full of fictional figures.")

    unstated = [
        label
        for label, missing in (
            ("household.birth_year", profile.birth_year is None),
            ("household.dependents", profile.dependents is None),
            ("income.annual_salary", profile.annual_salary is None),
            ("employer_plan", not profile.employer_plan_stated),
            ("ira.contributed_this_year", profile.ira_contributed is None),
            ("hsa", not profile.hsa_stated),
            ("investments.target_allocation_percent", profile.target_allocation is None),
            ("insurance", not profile.insurance_stated),
        )
        if missing
    ]
    if unstated:
        click.echo(f"  Not stated (the checks that need these will say so): {', '.join(unstated)}")
    review_days = rules.thresholds.integer("staleness", "profile_review_days")
    if profile.reviewed_on is None:
        click.echo("  Tip: set reviewed_on so stale figures can be noticed.")
    elif (today - profile.reviewed_on).days > review_days:
        click.echo(f"  ⚠ Last reviewed {profile.reviewed_on}, over {review_days} days ago.")


def _profile_is_example() -> bool:
    path = profile_path()
    return path.exists() and path.read_bytes() == example_profile_path().read_bytes()


@cli.command("check")
@click.option("--as-of", "as_of", default=None, help="ISO date. Defaults to today.")
@click.option("--only", default=None, help="Comma-separated check ids, e.g. F3.1,F3.4.")
@click.option(
    "--verbose", "-v", is_flag=True, help="Show the inputs and assumptions behind each figure."
)
def check_cmd(as_of, only, verbose) -> None:
    """Run the observation engine (F3.1–F3.10).

    Read-only: it analyzes recorded data and never acts on anything.
    """
    when = _date(as_of)
    wanted = [part.strip().upper() for part in only.split(",") if part.strip()] if only else None
    with connect() as conn:
        snapshot = build_snapshot(conn, as_of=when)
    try:
        observations = run_checks(snapshot, wanted)
    except ValueError as exc:
        _fail(f"{exc}. Valid: {', '.join(check_id for check_id, _, _ in CHECKS)}")

    if _profile_is_example():
        click.echo(
            "⚠ Your profile is still the unmodified example — results use FICTIONAL figures."
        )
        click.echo(f"  Edit {profile_path()}\n")
    click.echo(format_observations(observations, as_of=when, verbose=verbose))
    if any(o.status is Status.ERROR for o in observations):
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    cli()
