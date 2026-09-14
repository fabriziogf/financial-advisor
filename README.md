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

**M1 (observation engine) implemented.** `fa check` runs ten deterministic checks —
emergency fund, cash drag, tax-advantaged space and employer match, allocation,
asset location, fees, concentration, debt, insurance, and savings rate — and reports
what needs attention, what it can't assess yet, and exactly what to provide. M0 (local
database, CSV import, net worth statement) is complete.

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

uv run fa profile init       # salary, benefits, targets — stored outside the repo
uv run fa terms --account "Checking" --rate 0.01      # APY or APR, as a percent
uv run fa holdings positions.csv --account "Brokerage"
uv run fa rates refresh      # one request to FRED; sends nothing about you
uv run fa check              # --verbose shows the assumptions behind each figure
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
- **Checks say when they can't tell.** A check missing an input reports
  *insufficient data* and names what to add, rather than guessing. Every figure is
  computed by tested code and shown with the inputs and assumptions behind it.

## Not financial advice

This software produces educational analysis of your own data for your own use. It is
not a registered investment adviser, it does not know what it doesn't know, and its
output should not be treated as a substitute for a licensed human professional —
particularly for tax, estate, and insurance decisions.

## License

MIT — see [LICENSE](LICENSE).
