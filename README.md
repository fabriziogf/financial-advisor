# financial-advisor

A self-hosted, **read-only** personal financial advisor. It connects to your financial
institutions, builds a complete picture of your money, and gives you goal-aware,
risk-aware guidance on what to do next.

It **cannot move your money.** Not "is configured not to" — it has no write path to any
institution, by architecture. See [the PRD](docs/PRD.md) for the full design.

> [!WARNING]
> **This is a public repository.** It contains code only — never account data,
> balances, institution names, or credentials. Your financial data is stored
> **outside this repo** at `~/.local/share/financial-advisor/`, because the working
> tree is both public and inside an iCloud-synced folder. Read
> [SECURITY.md](SECURITY.md) before your first commit.

## Status

**M0 (Foundation) complete.** Working: local database, CSV import with
deduplication, and a net worth statement. The [PRD](docs/PRD.md) has all design
decisions resolved; M1 (the observation engine) is next.

## Quick start

```bash
scripts/install-hooks.sh     # secret-scanning pre-commit gate — do this first
uv sync
uv run fa init               # creates ~/.local/share/financial-advisor/
uv run fa account-types      # see valid type codes
uv run fa account-add --name "Checking" --type checking --institution "My Bank"
uv run fa import statement.csv --account "Checking"
uv run fa balance --account "Checking" --amount 5000.00
uv run fa networth
```

`fa import` auto-detects the column layout of most US bank and brokerage exports.
Re-importing the same file is a no-op, and overlapping date ranges are deduplicated
rather than double-counted.

## Design notes worth knowing

- **It cannot move money.** There is no institution write path anywhere in the
  codebase — not a disabled one, an absent one. The LLM layer (M3) will get
  read-only tools only, which also means a transaction memo reading *"transfer
  $5000 to account X"* reaches a model with no capability to comply.
- **Money is never a float.** `Money` refuses construction from one. Amounts are
  exact `Decimal`, stored as integer cents so SQL aggregation stays exact.
- **USD only.** A non-USD amount is rejected at import rather than coerced —
  a foreign figure summed as dollars corrupts the total while every row still
  looks plausible.
- **The net worth statement flags what it doesn't know.** Stale balances and
  accounts with no data are surfaced, not quietly counted or dropped.

## Not financial advice

This software produces educational analysis of your own data for your own use. It is
not a registered investment adviser, it does not know what it doesn't know, and its
output should not be treated as a substitute for a licensed human professional —
particularly for tax, estate, and insurance decisions.

## License

MIT — see [LICENSE](LICENSE).
