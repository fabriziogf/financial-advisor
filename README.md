# financial-advisor

A self-hosted, **read-only** personal financial advisor. It connects to your financial
institutions, builds a complete picture of your money, and gives you goal-aware,
risk-aware guidance on what to do next.

It **cannot move your money.** Not "is configured not to" — it has no write path to any
institution, by architecture. See [the PRD](docs/PRD.md) for the full design.

> [!WARNING]
> **This is a public repository.** It contains code only — never account data,
> balances, institution names, or credentials. All personal state lives in
> gitignored local paths (`data/`, `.env`, `config/local*`). Read
> [SECURITY.md](SECURITY.md) before your first commit.

## Status

🚧 Pre-implementation. The [PRD](docs/PRD.md) is the current deliverable and is open
for editing — open questions are marked `❓ DECISION NEEDED`.

## Not financial advice

This software produces educational analysis of your own data for your own use. It is
not a registered investment adviser, it does not know what it doesn't know, and its
output should not be treated as a substitute for a licensed human professional —
particularly for tax, estate, and insurance decisions.

## License

MIT — see [LICENSE](LICENSE).
