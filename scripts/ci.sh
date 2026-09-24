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
