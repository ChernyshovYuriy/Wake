#!/usr/bin/env bash
#
# system/install-services.sh
# ==========================
# Installs this repo's systemd units into /etc/systemd/system/, filling in the
# service user and the app directory, and reloads systemd. Run after
# system/setup-pi.sh, and again after `git pull` whenever a unit file changes.
#
# Usage:
#   sudo bash system/install-services.sh
#   sudo HLSIGNALS_USER=pi HLSIGNALS_DIR=/home/pi/dev/hl-whale-signals bash system/install-services.sh
#
# Defaults: the user who invoked sudo, and this repo's directory.
#
# Does NOT enable or start anything that isn't already enabled: existing
# timers keep their current state and pick up the new unit files on their
# next run. First deployment: run the `enable --now` commands in system/info.

set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo bash system/install-services.sh" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HLSIGNALS_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SERVICE_USER="${HLSIGNALS_USER:-${SUDO_USER:-pi}}"

if [ ! -x "$APP_DIR/.venv/bin/hlsignals" ]; then
    echo "$APP_DIR/.venv/bin/hlsignals not found: run system/setup-pi.sh first (as $SERVICE_USER)." >&2
    exit 1
fi

echo "Installing units for user '$SERVICE_USER', app dir '$APP_DIR' ..."
for unit in "$SCRIPT_DIR"/hlsignals-*.service "$SCRIPT_DIR"/hlsignals-*.timer; do
    target="/etc/systemd/system/$(basename "$unit")"
    sed -e "s|@USER@|$SERVICE_USER|g" -e "s|@APP_DIR@|$APP_DIR|g" "$unit" > "$target"
    echo "  $target"
done

echo "Reloading systemd ..."
systemctl daemon-reload

echo
echo "Done. First deployment? Enable everything with the commands in system/info."
echo "Check with:  systemctl list-timers --all | grep hlsignals"
