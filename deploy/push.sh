#!/usr/bin/env bash
#
# Deploy / push the sensor_reader app to a Windows machine over SSH.
#
# Machine-specific target info (host, user, paths) lives in deploy/local.env —
# copy deploy/local.env.example and fill it in. local.env is gitignored, so the
# repo itself carries no private network details.
#
# First run does a full bootstrap (creates the dir, venv, installs deps, registers
# the startup task). Every later run is an incremental push: it stops the running
# app, syncs the latest code, refreshes deps, and relaunches. config.toml and
# sensor.db on the target are never overwritten.
#
# Usage:   deploy/push.sh
# Override target:  REMOTE=user@host deploy/push.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Machine-specific settings (gitignored); env vars set by the caller win.
if [ -f "$ROOT/deploy/local.env" ]; then
  _remote="${REMOTE:-}" _win_user="${WIN_USER:-}" _win_dir="${WIN_DIR:-}" _win_py="${WIN_PY:-}"
  # shellcheck source=/dev/null
  . "$ROOT/deploy/local.env"
  REMOTE="${_remote:-${REMOTE:-}}"; WIN_USER="${_win_user:-${WIN_USER:-}}"
  WIN_DIR="${_win_dir:-${WIN_DIR:-}}"; WIN_PY="${_win_py:-${WIN_PY:-}}"
fi

REMOTE="${REMOTE:?set REMOTE (user@host) or create deploy/local.env from deploy/local.env.example}"
WIN_USER="${WIN_USER:-${REMOTE%%@*}}"                          # Windows profile name; default: the ssh user
WIN_DIR="${WIN_DIR:-C:\\Users\\${WIN_USER}\\sensor_reader}"    # native Windows path (for cmd)
SCP_DIR="$(printf '%s' "$WIN_DIR" | tr '\\' '/')"              # forward-slash path (for scp)
WIN_PY="${WIN_PY:-python}"                                     # python used to bootstrap the venv
HOST="${REMOTE#*@}"

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
ssh "$REMOTE" "cd /d \"$WIN_DIR\" && remote-update.bat \"$WIN_PY\"" 2>&1 | strip_ssh_noise | tr -d '\r'

echo "==> verifying the app responds"
ok=""
for i in 1 2 3 4 5 6; do
  sleep 3
  if ssh "$REMOTE" "curl -s -m 5 http://127.0.0.1:8787/api/current" 2>/dev/null | strip_ssh_noise | grep -q '"online"'; then
    ok=1; break
  fi
done
if [ -n "$ok" ]; then
  echo "==> SUCCESS: app is serving on the target (http://$HOST:8787)"
else
  echo "!!! app did not respond yet; check the log:"
  echo "    ssh $REMOTE \"type \\\"$WIN_DIR\\app.log\\\"\""
  exit 1
fi
rm -rf "$STAGE" "$(dirname "$TARBALL")"
