#!/usr/bin/env bash
# Runs every quality gate from IMPLEMENTATION_PLAN.md §0.3. Fails on the first red gate.
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=".venv/bin"
[ -x "$BIN/python" ] || BIN="$(dirname "$(command -v python)")"

echo "== ruff";        "$BIN/ruff" check src tests scripts
echo "== ruff format"; "$BIN/ruff" format --check src tests scripts
echo "== mypy";        "$BIN/mypy"
if [ -f .importlinter ]; then
  echo "== import-linter"; "$BIN/lint-imports"
fi
echo "== duplication"
"$BIN/pylint" --disable=all --enable=duplicate-code --min-similarity-lines=6 \
  --ignore=fixtures src tests
echo "== pytest";      "$BIN/pytest" --cov --cov-branch --cov-fail-under=95 "$@"
# IMPLEMENTATION_PLAN.md §7: 100% branch coverage for the pure core, on top of 95% overall.
# The tests themselves too (tests/live excluded in pyproject): no dead helpers or fake branches.
# One report over these paths reaches 100% only if every file in it does.
FULL_COVERAGE="src/hlsignals/core/*,src/hlsignals/domain/*,src/hlsignals/signals/*,\
src/hlsignals/wallets/scoring/*,src/hlsignals/wallets/filters.py,src/hlsignals/session/*,\
src/hlsignals/backtest/asof.py,tests/*"
echo "== coverage 100% (pure core)"
"$BIN/coverage" report --include="$FULL_COVERAGE" --fail-under=100
