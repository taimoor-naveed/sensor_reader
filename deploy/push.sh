#!/usr/bin/env bash
#
# Deploy / push the sensor_reader app to the Ubuntu machine over SSH.
#
# Target info (host, app dir, python) lives in deploy/local.env — copy
# deploy/local.env.example and fill it in. local.env is gitignored, so the repo
# itself carries no private network details.
#
# One-time prep on a fresh machine:  deploy/bootstrap.sh  (apt deps, linger, ufw)
#
# First run creates the dir, venv, deps and the systemd user unit. Every later
# run is an incremental push: stop the service, sync the latest code, refresh
# deps, restart. config.toml and sensor.db on the target are never overwritten.
#
# Usage:   deploy/push.sh
# Override target:  REMOTE=user@host deploy/push.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Machine-specific settings (gitignored); env vars set by the caller win.
if [ -f "$ROOT/deploy/local.env" ]; then
  _remote="${REMOTE:-}" _app_dir="${APP_DIR:-}" _py="${REMOTE_PY:-}"
  # shellcheck source=/dev/null
  . "$ROOT/deploy/local.env"
  REMOTE="${_remote:-${REMOTE:-}}"; APP_DIR="${_app_dir:-${APP_DIR:-}}"
  REMOTE_PY="${_py:-${REMOTE_PY:-}}"
fi

REMOTE="${REMOTE:?set REMOTE (user@host) or create deploy/local.env from deploy/local.env.example}"
APP_DIR="${APP_DIR:-sensor_reader}"   # relative paths are resolved under the remote $HOME
REMOTE_PY="${REMOTE_PY:-python3}"     # python used to create the venv
SERVICE="${SERVICE:-sensor-reader}"
HOST="${REMOTE#*@}"

strip_ssh_noise() { grep -v "post-quantum\|store now\|may need to be upgraded\|openssh.com/pq\|vulnerable" || true; }

echo "==> packaging code from $ROOT"
STAGE="$(mktemp -d)"
( cd "$ROOT" && tar cf - --exclude='__pycache__' --exclude='*.pyc' lywsd03mmc_monitor ) | ( cd "$STAGE" && tar xf - )
cp "$ROOT/deploy/requirements-runtime.txt" "$STAGE/"
cp "$ROOT/deploy/remote-update.sh"          "$STAGE/"
chmod +x "$STAGE/remote-update.sh"
TARBALL="$(mktemp -d)/deploy.tar.gz"
tar czf "$TARBALL" -C "$STAGE" .

echo "==> ensuring remote directory $APP_DIR"
ssh "$REMOTE" "mkdir -p '$APP_DIR'" 2>&1 | strip_ssh_noise

echo "==> uploading package"
scp -q "$TARBALL" "$REMOTE:$APP_DIR/_deploy.tar.gz"
ssh "$REMOTE" "cd '$APP_DIR' && tar xzf _deploy.tar.gz && rm -f _deploy.tar.gz" 2>&1 | strip_ssh_noise

echo "==> provisioning config.toml (only if missing on target)"
if ssh "$REMOTE" "test -f '$APP_DIR/config.toml' && echo EXISTS" 2>/dev/null | grep -q EXISTS; then
  echo "    config.toml already present — left untouched"
else
  scp -q "$ROOT/config.toml" "$REMOTE:$APP_DIR/config.toml"
  echo "    config.toml uploaded"
fi

echo "==> running remote update (stop / venv / deps / unit / start)"
ssh "$REMOTE" "cd '$APP_DIR' && ./remote-update.sh '$REMOTE_PY'" 2>&1 | strip_ssh_noise | tr -d '\r'

echo "==> verifying the app responds"
HEALTH_PY='import urllib.request,sys;sys.stdout.write(urllib.request.urlopen("http://127.0.0.1:8787/api/current",timeout=5).read().decode())'
ok=""
for _ in 1 2 3 4 5 6; do
  sleep 3
  if ssh "$REMOTE" "cd '$APP_DIR' && .venv/bin/python -c '$HEALTH_PY'" 2>/dev/null | strip_ssh_noise | grep -q '"online"'; then
    ok=1; break
  fi
done
rm -rf "$STAGE" "$(dirname "$TARBALL")"

if [ -n "$ok" ]; then
  echo "==> SUCCESS: app is serving on the target (http://$HOST:8787)"
  # A dead BLE radio still lets the dashboard serve stored data — surface it here
  # instead of letting the user discover it as a stuck "offline" sensor.
  if ssh "$REMOTE" "tail -n 40 '$APP_DIR/app.log' 2>/dev/null" 2>/dev/null | grep -q "BLE scanner failed to start"; then
    echo "!!! WARNING: the BLE scanner is failing to start — the dashboard serves stored data only."
    echo "    Check Bluetooth access:  ssh $REMOTE \"systemctl status bluetooth; bluetoothctl show\""
    echo "    Full log:                ssh $REMOTE \"tail -n 60 '$APP_DIR/app.log'\""
  fi
else
  echo "!!! app did not respond yet; check the service and log:"
  echo "    ssh $REMOTE \"systemctl --user status $SERVICE --no-pager\""
  echo "    ssh $REMOTE \"tail -n 60 '$APP_DIR/app.log'\""
  exit 1
fi
