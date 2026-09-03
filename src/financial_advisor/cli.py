"""`fa` — command line for the advisor.

Advisory only (PRD §4/G5). Nothing here can move money; there is no institution
write path anywhere in the codebase for a command to call.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import click

from .db.connection import connect, initialize, is_initialized
from .db.store import create_account, find_account, list_accounts, set_balance
from .importers.csv_import import ImportError_, SignConvention, import_file
from .money import Money, MoneyError
from .paths import data_dir, database_path
from .reports.net_worth import compute_net_worth, format_net_worth


def _fail(message: str) -> None:
    raise click.ClickException(message)


@click.group()
@click.version_option(package_name="financial-advisor")
def cli() -> None:
    """Read-only personal financial advisor."""


@cli.command()
def init() -> None:
    """Create the local database."""
    if is_initialized():
        click.echo(f"Already initialized: {database_path()}")
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
    default="taxable",
)
@click.option("--manual", is_flag=True, help="Declared asset with hand-entered values.")
def account_add(name, type_code, institution, tax_treatment, manual) -> None:
    """Add an account. Use --manual for property and other declared assets."""
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


if __name__ == "__main__":  # pragma: no cover
    cli()
