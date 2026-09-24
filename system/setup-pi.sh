#!/usr/bin/env bash
#
# system/setup-pi.sh
# ==================
# One-time setup of hl-whale-signals on a Raspberry Pi (or any Linux box):
# checks the Python version, creates .venv, installs the package, and creates
# the data directories. Run as the service user (not root), from anywhere:
#
#   bash system/setup-pi.sh
#
# Then install the systemd units:  sudo bash system/install-services.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "hl-whale-signals needs Python >= 3.12; $("$PYTHON" --version 2>&1) found." >&2
    echo "Raspberry Pi OS Bookworm ships 3.11. Options:" >&2
    echo "  - upgrade to Raspberry Pi OS Trixie (Python 3.13), or" >&2
    echo "  - install 3.12+ with uv (https://docs.astral.sh/uv/) and rerun:" >&2
    echo "      PYTHON=\"\$(uv python find 3.12)\" bash system/setup-pi.sh" >&2
    exit 1
fi

echo "Using $("$PYTHON" --version) in $APP_DIR"
cd "$APP_DIR"
"$PYTHON" -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
mkdir -p data/reports

echo
echo "Checking the installation ..."
.venv/bin/hlsignals --config config/pi.toml --help > /dev/null
echo "OK. Next: sudo bash system/install-services.sh   (see system/info)"
