#!/usr/bin/env bash
#
# Deploy / push the sensor_reader app to the Windows home PC over SSH.
#
# First run does a full bootstrap (creates the dir, venv, installs deps, registers
# the startup task). Every later run is an incremental push: it stops the running
# app, syncs the latest code, refreshes deps, and relaunches. config.toml and
# sensor.db on the target are never overwritten.
#
# Usage:   deploy/push.sh
# Override target:  REMOTE=<user>@<lan-ip> deploy/push.sh
#
set -euo pipefail

REMOTE="${REMOTE:-<user>@<host>}"
WIN_DIR='C:\Users\<user>\sensor_reader'        # native Windows path (for cmd)
SCP_DIR='C:/Users/<user>/sensor_reader'        # forward-slash path (for scp)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

strip_ssh_noise() { grep -v "post-quantum\|store now\|may need to be upgraded\|openssh.com/pq\|vulnerable" || true; }

echo "==> packaging code from $ROOT"
STAGE="$(mktemp -d)"
( cd "$ROOT" && tar cf - --exclude='__pycache__' --exclude='*.pyc' lywsd03mmc_monitor ) | ( cd "$STAGE" && tar xf - )
cp "$ROOT/deploy/requirements-runtime.txt" "$STAGE/"
cp "$ROOT/deploy/remote-update.bat"         "$STAGE/"
TARBALL="$(mktemp -d)/deploy.tar.gz"
tar czf "$TARBALL" -C "$STAGE" .

echo "==> ensuring remote directory $WIN_DIR"
ssh "$REMOTE" "if not exist \"$WIN_DIR\" mkdir \"$WIN_DIR\"" 2>&1 | strip_ssh_noise

echo "==> uploading package"
scp -q "$TARBALL" "$REMOTE:$SCP_DIR/_deploy.tar.gz"
ssh "$REMOTE" "cd /d \"$WIN_DIR\" && tar xzf _deploy.tar.gz && del _deploy.tar.gz" 2>&1 | strip_ssh_noise

echo "==> provisioning config.toml (only if missing on target)"
if ssh "$REMOTE" "if exist \"$WIN_DIR\\config.toml\" (echo EXISTS)" 2>/dev/null | grep -q EXISTS; then
  echo "    config.toml already present — left untouched"
else
  scp -q "$ROOT/config.toml" "$REMOTE:$SCP_DIR/config.toml"
  echo "    config.toml uploaded"
fi

echo "==> running remote update (stop / venv / deps / task / start)"
ssh "$REMOTE" "cd /d \"$WIN_DIR\" && remote-update.bat" 2>&1 | strip_ssh_noise | tr -d '\r'

echo "==> verifying the app responds"
ok=""
for i in 1 2 3 4 5 6; do
  sleep 3
  if ssh "$REMOTE" "curl -s -m 5 http://127.0.0.1:8787/api/current" 2>/dev/null | strip_ssh_noise | grep -q '"online"'; then
    ok=1; break
  fi
done
if [ -n "$ok" ]; then
  echo "==> SUCCESS: app is serving on the target (http://<host>:8787)"
else
  echo "!!! app did not respond yet; check the log:"
  echo "    ssh $REMOTE \"type \\\"$WIN_DIR\\app.log\\\"\""
  exit 1
fi
rm -rf "$STAGE" "$(dirname "$TARBALL")"
