#!/usr/bin/env bash
#
# One-time host prep for the sensor_reader app. Runs ON the Ubuntu machine as root.
# Idempotent — safe to re-run.
#
#   sudo ./bootstrap-remote.sh [app-user]      # app-user defaults to $SUDO_USER
#
# Normally invoked for you by deploy/bootstrap.sh from the Mac.
#
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "!!! run as root:  sudo $0 ${1:-}" >&2
  exit 1
fi

APP_USER="${1:-${SUDO_USER:-}}"
[ -n "$APP_USER" ] || { echo "!!! pass the app user name as the first argument" >&2; exit 1; }
id "$APP_USER" >/dev/null || exit 1
APP_UID="$(id -u "$APP_USER")"
NEEDS_REBOOT=""

echo "==> [1/5] Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# python3-venv: the venv the app runs in. bluez: BlueZ + bluetoothctl (bleak talks
# to bluetoothd over D-Bus). curl: health checks. avahi-daemon: <host>.local names.
apt-get install -y -qq python3-venv python3-pip bluez curl avahi-daemon

echo "==> [2/5] Enabling Bluetooth"
systemctl enable --now bluetooth
if getent group bluetooth >/dev/null && ! id -nG "$APP_USER" | tr ' ' '\n' | grep -qx bluetooth; then
  usermod -aG bluetooth "$APP_USER"
  echo "    added $APP_USER to the 'bluetooth' group (takes effect after a reboot)"
  NEEDS_REBOOT=1
fi

echo "==> [3/5] Enabling lingering for $APP_USER"
# Without this the systemd *user* manager (and the app with it) is torn down when
# the last session ends, and nothing starts at boot.
loginctl enable-linger "$APP_USER"

echo "==> [4/5] Opening the dashboard port (8787/tcp)"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 8787/tcp >/dev/null && echo "    ufw rule added"
else
  echo "    ufw is inactive — no rule needed"
fi

echo "==> [5/5] Checks"
printf '    python3: %s\n' "$(python3 -V 2>&1)"
printf '    linger:  %s\n' "$(loginctl show-user "$APP_USER" -p Linger --value 2>/dev/null || echo unknown)"
printf '    runtime: %s\n' "$([ -d "/run/user/$APP_UID" ] && echo "/run/user/$APP_UID present" || echo "MISSING — reboot before deploying")"
if sudo -u "$APP_USER" timeout 10 bluetoothctl show 2>&1 | grep -q "Powered: yes"; then
  echo "    BLE:     adapter powered and reachable as $APP_USER"
else
  echo "    BLE:     could NOT confirm adapter access as $APP_USER — check after reboot:"
  echo "               bluetoothctl show     # expect 'Powered: yes'"
fi

echo
echo "bootstrap done."
[ -n "$NEEDS_REBOOT" ] && cat <<'MSG'
>>> Reboot recommended: the new 'bluetooth' group membership only reaches the
>>> service after the user manager restarts, and a reboot also proves that
>>> unattended autostart works.  sudo reboot
MSG
exit 0
