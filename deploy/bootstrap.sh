#!/usr/bin/env bash
#
# One-time host prep for a fresh Ubuntu target, driven from the Mac.
# Copies deploy/bootstrap-remote.sh to the target and runs it under sudo on a TTY
# (so a sudo password prompt works). Idempotent — safe to re-run.
#
# Usage:  deploy/bootstrap.sh          # target from deploy/local.env
#         REMOTE=user@host deploy/bootstrap.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT/deploy/local.env" ]; then
  _remote="${REMOTE:-}"
  # shellcheck source=/dev/null
  . "$ROOT/deploy/local.env"
  REMOTE="${_remote:-${REMOTE:-}}"
fi
REMOTE="${REMOTE:?set REMOTE (user@host) or create deploy/local.env from deploy/local.env.example}"
APP_USER="${REMOTE%%@*}"

echo "==> uploading bootstrap script to $REMOTE"
scp -q "$ROOT/deploy/bootstrap-remote.sh" "$REMOTE:/tmp/sensor-reader-bootstrap.sh"

echo "==> running it with sudo (you may be asked for the sudo password)"
ssh -t "$REMOTE" "chmod +x /tmp/sensor-reader-bootstrap.sh && sudo /tmp/sensor-reader-bootstrap.sh '$APP_USER'; rm -f /tmp/sensor-reader-bootstrap.sh"

echo
echo "Next:  deploy/push.sh          # ship the code and start the service"
echo "Then:  deploy/restore-db.sh    # merge the backed-up sensor history in"
