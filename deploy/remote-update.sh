#!/usr/bin/env bash
#
# Runs ON the Ubuntu machine (in the app dir) after push.sh extracts new code.
# Stops the old instance, ensures the venv + deps, (re)installs the systemd user
# unit, and starts it again.
#
# Arg 1 (optional): python interpreter used to create the venv (default python3).
#
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$PWD"
PY="${1:-python3}"
SERVICE="sensor-reader"
UNIT_DIR="$HOME/.config/systemd/user"

# `systemctl --user` over a non-interactive SSH command needs this; pam_systemd
# normally sets it, but don't rely on it.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
ME="$(id -un)"

# If any step after the stop fails, put the previous version back in service rather
# than leaving the dashboard down.
trap 'code=$?; [ $code -eq 0 ] || { echo "!!! update failed (exit $code) — restarting the previous version"; systemctl --user start "$SERVICE" 2>/dev/null || true; }' EXIT

echo "[1/5] Stopping any running instance..."
systemctl --user stop "$SERVICE" 2>/dev/null || true
# Belt and braces: kill a stray manual run from the same directory.
pkill -f "$APP_DIR/.venv/bin/python -m lywsd03mmc_monitor" 2>/dev/null || true

echo "[2/5] Ensuring virtual environment..."
if [ ! -x ".venv/bin/python" ]; then
  "$PY" -m venv .venv || {
    echo "!!! venv creation failed — install it with: sudo apt install -y python3-venv" >&2
    exit 1
  }
fi

echo "[3/5] Installing/refreshing dependencies..."
.venv/bin/python -m pip install --disable-pip-version-check -q -U pip
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-runtime.txt

echo "[4/5] Installing systemd user unit ($SERVICE)..."
mkdir -p "$UNIT_DIR"
# Ordering against bluetooth.target is not expressible in the user manager (it is
# a system unit), so the app's own retry loop handles a radio that isn't ready yet.
cat > "$UNIT_DIR/$SERVICE.service" <<UNIT
[Unit]
Description=LYWSD03MMC sensor monitor + dashboard
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m lywsd03mmc_monitor
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload

echo "[5/5] Enabling + starting the service..."
systemctl --user enable "$SERVICE" >/dev/null
systemctl --user restart "$SERVICE"

if ! loginctl show-user "$ME" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo "!!! WARNING: lingering is OFF for $ME — the service will be killed when your"
  echo "    last session ends and will NOT start at boot. Fix once with:"
  echo "      sudo loginctl enable-linger $ME"
fi

echo "remote-update done."
