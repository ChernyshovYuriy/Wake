#!/usr/bin/env bash
# Runs every quality gate from IMPLEMENTATION_PLAN.md §0.3. Fails on the first red gate.
set -euo pipefail
cd "$(dirname "$0")/.."
# Each tool runs as a module of one interpreter: .venv's when it has the dev tools (the
# Pi's .venv holds runtime deps only), else the system python3.
if [ -x .venv/bin/ruff ]; then
  PY=.venv/bin/python
  LINT_IMPORTS=.venv/bin/lint-imports
else
  PY=python3
  LINT_IMPORTS=lint-imports
fi
echo "== using $PY"

echo "== ruff";        "$PY" -m ruff check src tests scripts
echo "== ruff format"; "$PY" -m ruff format --check src tests scripts
echo "== mypy";        "$PY" -m mypy
if [ -f .importlinter ]; then
  echo "== import-linter"; "$LINT_IMPORTS"
fi
echo "== duplication"
"$PY" -m pylint --disable=all --enable=duplicate-code --min-similarity-lines=6 \
  --ignore=fixtures src tests
echo "== pytest";      "$PY" -m pytest --cov --cov-branch --cov-fail-under=95 "$@"
# IMPLEMENTATION_PLAN.md §7: 100% branch coverage for the pure core, on top of 95% overall.
# The tests themselves too (tests/live excluded in pyproject): no dead helpers or fake branches.
# One report over these paths reaches 100% only if every file in it does.
FULL_COVERAGE="src/hlsignals/core/*,src/hlsignals/domain/*,src/hlsignals/signals/*,\
src/hlsignals/wallets/scoring/*,src/hlsignals/wallets/filters.py,src/hlsignals/session/*,\
src/hlsignals/backtest/asof.py,tests/*"
echo "== coverage 100% (pure core)"
"$PY" -m coverage report --include="$FULL_COVERAGE" --fail-under=100
