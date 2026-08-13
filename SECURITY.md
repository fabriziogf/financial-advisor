# Security & Privacy Rules

This repo is **public**. The rules below are non-negotiable and should be enforced by
tooling, not memory.

## The one rule

**Code is public. Data is local. They never mix.**

Nothing that describes *your* finances — institution names, account numbers, balances,
transactions, employer, net worth, goals with real dollar figures, generated advice —
belongs in a tracked file. Ever.

## What this means in practice

| Category | Where it lives | Tracked? |
|---|---|---|
| Application code | `src/` | ✅ yes |
| Schemas, rules, tax tables | `src/`, `rules/` | ✅ yes |
| Config *shape* | `*.example.yml` | ✅ yes |
| Your actual config | `config/local.yml` | ❌ never |
| Credentials, API tokens | `.env`, OS keychain | ❌ never |
| Account & transaction data | `data/` (encrypted) | ❌ never |
| Generated advice & reports | `reports/` | ❌ never |
| Test fixtures | `tests/fixtures/` — **synthetic only** | ✅ yes |

Test fixtures must be fabricated. Do not "anonymize" a real export by editing names;
transaction patterns, amounts, and timing are themselves identifying.

## Required controls

- [ ] `.gitignore` with deny-by-default posture for data paths *(done)*
- [ ] `gitleaks` (or `trufflehog`) as a **pre-commit hook** — blocks secrets locally
- [ ] `gitleaks` in CI — blocks secrets that bypass the hook
- [ ] Secret scanning + push protection enabled in GitHub repo settings
- [ ] Credentials in the macOS Keychain, not in `.env`, once past prototype
- [ ] Local database encrypted at rest (SQLCipher or age-encrypted volume)
- [ ] A `make check-clean` target that greps the staging area for balance-shaped
      strings and known institution names before commit

## If data leaks into a commit

Assume the moment it hits GitHub it is public and permanent — forks, caches, and
scrapers make deletion unreliable.

1. Rotate every credential involved, immediately. This is the only step that
   reliably helps.
2. Delete the repo or make it private, then rewrite history
   (`git filter-repo`) — do not rely on a follow-up commit that "removes" the file.
3. Contact GitHub Support to purge cached views of the affected commits.

## Threat model note

The LLM advisory layer sees your full financial picture. Whatever model provider you
route to receives that data. Choose deliberately — see the PRD's
"❓ DECISION NEEDED: model hosting" item.
