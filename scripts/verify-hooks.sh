#!/usr/bin/env bash
# Adversarial test for the pre-commit gate (PRD P2).
#
# An untested security control is not a control. This stages known-bad content and
# asserts the hook REJECTS it, then stages known-good content and asserts the hook
# ACCEPTS it — a gate that blocks everything is as broken as one that blocks nothing,
# and only testing both directions catches that.
#
# Runs entirely inside a scratch clone under $TMPDIR. It never commits, stages, or
# otherwise touches your real working tree.

set -uo pipefail

REPO_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; NC=$'\033[0m'

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/fa-hooktest.XXXXXX")"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

pass=0; fail=0

# Build a scratch repo carrying the same hook + config as the real one.
git init -q "$SCRATCH/repo"
cd "$SCRATCH/repo"
git config user.email "test@example.invalid"
git config user.name "Hook Test"
mkdir -p .githooks
cp "$REPO_ROOT/.githooks/pre-commit" .githooks/
cp "$REPO_ROOT/.gitleaks.toml" .
cp "$REPO_ROOT/.gitignore" .
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks

# Seed a commit so `git diff --cached` has a parent to compare against.
#
# The scaffolding must be COMMITTED, not left untracked: each check ends with
# `git clean -fd`, which deletes untracked files — including .gitleaks.toml and
# .githooks/ themselves. Leaving them untracked silently removes the hook after the
# first case, so every later commit succeeds and the suite reports "allow" for
# everything. Tracked files survive reset --hard and clean.
echo "seed" > seed.txt
git add seed.txt .gitleaks.toml .gitignore .githooks/pre-commit
git -c core.hooksPath=/dev/null commit -qm "seed"

# check <expectation> <name> — expects "block" or "allow"; content is pre-staged.
check() {
    local expect="$1" name="$2" out rc
    out=$(git commit -qm "hook test: $name" 2>&1); rc=$?

    if [ "$rc" -ne 0 ]; then git reset -q --hard HEAD; fi

    if { [ "$expect" = "block" ] && [ "$rc" -ne 0 ]; } || \
       { [ "$expect" = "allow" ] && [ "$rc" -eq 0 ]; }; then
        printf '  %s✓%s %-46s %s(%s)%s\n' "$GREEN" "$NC" "$name" "$DIM" "$expect" "$NC"
        pass=$((pass + 1))
    else
        printf '  %s✗%s %-46s %sexpected %s, got rc=%s%s\n' \
            "$RED" "$NC" "$name" "$RED" "$expect" "$rc" "$NC"
        printf '%s\n' "$out" | sed 's/^/      /' | head -6
        fail=$((fail + 1))
    fi
    git reset -q --hard HEAD >/dev/null 2>&1
    git clean -qfd >/dev/null 2>&1
}

echo
echo "${BOLD}Verifying pre-commit gate${NC}"
echo

# --- Credentials (default gitleaks ruleset) -------------------------------
# NOTE: every value below is randomly generated filler, not a live credential.
#
# Deliberately NOT the canonical documentation examples (AKIAIOSFODNN7EXAMPLE and
# friends): gitleaks allowlists those precisely because they appear in docs, so a
# test built on them passes against a completely broken hook. Fixtures for a
# security test have to look like real secrets or they prove nothing.
printf 'aws_access_key_id = AKIA4XQ2FZ7NBVWQ3JLM\n' > cfg.txt
git add cfg.txt
check block "AWS key"

printf -- '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF0qN2Ux1nBqRLJmpDpKMBmhTFHtq\n-----END RSA PRIVATE KEY-----\n' > id_rsa_test
git add id_rsa_test
check block "private key"

# --- Project-specific credential rules ------------------------------------
printf 'ANTHROPIC_API_KEY=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA\n' > llm.txt
git add llm.txt
check block "Anthropic API key"

printf 'access_url = "https://user123:pass456@bridge.simplefin.org/simplefin"\n' > sf.txt
git add sf.txt
check block "SimpleFIN access URL"

# --- Personal data rules --------------------------------------------------
printf 'name,ssn\nJane Doe,123-45-6789\n' > people.txt
git add people.txt
check block "US SSN"

printf 'card: 4111111111111111\n' > card.txt
git add card.txt
check block "credit card number"

printf 'account_number: 000123456789\n' > acct.txt
git add acct.txt
check block "bank account number"

# --- Structural rules -----------------------------------------------------
mkdir -p data
printf 'date,amount\n2026-01-01,100.00\n' > data/statement.csv
git add -f data/statement.csv
check block "CSV force-added under data/"

mkdir -p rules
printf 'VTI:\n  name: Total Stock Market\n  shares: 47\n' > rules/securities.yml
git add rules/securities.yml
check block "holdings data in securities.yml"

# --- Control cases: these MUST be allowed ---------------------------------
# A gate that rejects everything is indistinguishable from one that works, unless
# legitimate content is also tested.
mkdir -p rules
printf 'VTI:\n  name: Vanguard Total Stock Market ETF\n  expense_ratio: 0.0003\n  asset_class:\n    us_equity: 1.0\n' > rules/securities.yml
git add rules/securities.yml
check allow "legitimate securities.yml (no holdings)"

printf '# Notes\n\nOrdinary project documentation.\n' > NOTES.md
git add NOTES.md
check allow "ordinary source file"

echo
if [ "$fail" -eq 0 ]; then
    echo "${GREEN}${BOLD}✓ all $pass checks passed${NC}"
    exit 0
else
    echo "${RED}${BOLD}✗ $fail of $((pass + fail)) checks failed${NC}"
    echo "  The commit gate is not behaving as specified. Fix before relying on it."
    exit 1
fi
