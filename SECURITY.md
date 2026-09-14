# Security & Privacy Rules

This repo is **public**. The rules below are non-negotiable and should be enforced by
tooling, not memory.

## The one rule

**Code is public. Data lives outside this repository. They never mix.**

Financial data is stored at `~/.local/share/financial-advisor/` (override with
`FA_DATA_DIR`), not in the working tree.

Two independent reasons, either sufficient:

1. **This repo is public.** Anything in the tree is one `git add -A` from being
   published permanently.
2. **This repo sits inside iCloud Drive.** `~/Documents` has Desktop & Documents
   sync enabled, so a database written into the tree would upload to Apple's
   servers and sync to every device on the account — silently, on first import.
   Unless Advanced Data Protection is on, iCloud Drive is encrypted with keys
   Apple holds. FileVault does not help here: the copy has already left the disk.

Keeping data out of the tree is a *structural* control. A file that isn't there
cannot be committed by mistake, and the protection doesn't depend on `.gitignore`
staying correct forever. The `data/` rules below are defence in depth, not the
primary barrier.

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
| Account & transaction data | `~/.local/share/financial-advisor/` | ❌ not in repo at all |
| Generated advice & reports | `~/.local/share/financial-advisor/` | ❌ not in repo at all |
| Your profile (salary, benefits, targets) | `~/.local/share/financial-advisor/profile.yml` | ❌ not in repo at all |
| Funds you actually hold | `~/.local/share/financial-advisor/securities.local.yml` | ❌ not in repo at all |
| Generic securities reference data | `rules/securities.yml` | ✅ yes — common funds only, never holdings |
| Thresholds and IRS limits | `rules/thresholds.yml`, `rules/limits/` | ✅ yes |
| Test fixtures | `tests/fixtures/` — **synthetic only** | ✅ yes |

Don't add the funds you own to `rules/securities.yml`, even without quantities: the
*list* of symbols in a public file discloses your holdings. Describe them in the local
overlay instead — same format, never committed.

Test fixtures must be fabricated. Do not "anonymize" a real export by editing names;
transaction patterns, amounts, and timing are themselves identifying.

## Required controls

- [x] `.gitignore` with deny-by-default posture for data paths
- [x] Data stored outside the working tree — the primary control
- [x] `gitleaks` as a **pre-commit hook** (`scripts/install-hooks.sh`)
- [x] **Adversarial test of the hook** (`scripts/verify-hooks.sh`) — an untested
      security control is not a control. This caught two silent failures on the
      day it was written: `gitleaks protect` is a no-op stub in 8.30 that passes
      everything, and fixtures built from canonical doc placeholders
      (`AKIAIOSFODNN7EXAMPLE`) are allowlisted by gitleaks and so pass against a
      completely broken hook.
- [x] `gitleaks` in CI, plus the adversarial suite re-run there — local hooks can
      be skipped with `--no-verify` and don't exist until installed
- [x] CI assertion that no data paths or stray `.csv`/`.db` files are tracked
- [x] Secret scanning + push protection enabled in GitHub repo settings
- [x] Disk encryption via FileVault; data directory `0700`, database `0600`
- [ ] Credentials in the macOS Keychain, not in `.env`, once past prototype
- [ ] SQLCipher — deferred; see PRD P4 for the reasoning and revisit trigger

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
