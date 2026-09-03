#!/usr/bin/env bash
# One-time setup for the local security gate (PRD P2).
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if ! command -v gitleaks >/dev/null 2>&1; then
    echo "gitleaks not found."
    if command -v brew >/dev/null 2>&1; then
        echo "Installing via Homebrew..."
        brew install gitleaks
    else
        echo "Install it first: https://github.com/gitleaks/gitleaks#installing"
        exit 1
    fi
fi

chmod +x .githooks/*
git config core.hooksPath .githooks

echo "✓ hooks installed (core.hooksPath -> .githooks)"
echo "✓ gitleaks $(gitleaks version)"
echo
echo "Verify it works:"
echo "  echo 'aws_secret_access_key = AKIAIOSFODNN7EXAMPLE' > /tmp/leak.txt"
echo "  git add -f /tmp/leak.txt   # should be blocked at commit"
