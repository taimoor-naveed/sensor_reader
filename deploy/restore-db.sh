#!/usr/bin/env bash
#
# Restore backed-up sensor history onto the deploy target.
#
# Default mode MERGES the backup into the live database (INSERT OR IGNORE on the
# timestamp primary keys): nothing already on the target is lost or overwritten,
# and re-running is a no-op. --replace swaps the file wholesale instead.
#
# Usage:
#   deploy/restore-db.sh                          # newest backups/*/sensor.db, merge
#   deploy/restore-db.sh path/to/sensor.db        # explicit source
#   deploy/restore-db.sh [src] --replace          # overwrite the target DB instead
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT/deploy/local.env" ]; then
  _remote="${REMOTE:-}" _app_dir="${APP_DIR:-}"
  # shellcheck source=/dev/null
  . "$ROOT/deploy/local.env"
  REMOTE="${_remote:-${REMOTE:-}}"; APP_DIR="${_app_dir:-${APP_DIR:-}}"
fi
REMOTE="${REMOTE:?set REMOTE (user@host) or create deploy/local.env from deploy/local.env.example}"
APP_DIR="${APP_DIR:-sensor_reader}"
SERVICE="${SERVICE:-sensor-reader}"

MODE="merge"
SRC=""
for arg in "$@"; do
  case "$arg" in
    --replace) MODE="replace" ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *)  SRC="$arg" ;;
  esac
done
if [ -z "$SRC" ]; then
  SRC="$(ls -1dt "$ROOT"/backups/*/sensor.db 2>/dev/null | head -n1 || true)"
  [ -n "$SRC" ] || { echo "!!! no backup found under $ROOT/backups/*/sensor.db — pass one explicitly" >&2; exit 1; }
fi
[ -f "$SRC" ] || { echo "!!! no such file: $SRC" >&2; exit 1; }

strip_ssh_noise() { grep -v "post-quantum\|store now\|may need to be upgraded\|openssh.com/pq\|vulnerable" || true; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_SYSTEMCTL="export XDG_RUNTIME_DIR=\${XDG_RUNTIME_DIR:-/run/user/\$(id -u)}; systemctl --user"

# Never exit with the service left stopped: the dashboard should keep serving whatever
# is in the database even if the restore itself went wrong.
STOPPED=""
DONE=""
cleanup() {
  if [ -n "$STOPPED" ] && [ -z "$DONE" ]; then
    echo "!!! restore aborted — restarting the service on the target"
    ssh "$REMOTE" "$REMOTE_SYSTEMCTL start '$SERVICE'" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "==> checking the backup locally: $SRC"
python3 - "$SRC" <<'PYEOF'
import sqlite3, sys
from datetime import datetime, timezone
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
status = con.execute("PRAGMA integrity_check").fetchone()[0]
if status != "ok":
    sys.exit(f"!!! integrity_check failed: {status}")
n, lo, hi = con.execute("SELECT count(*), min(ts), max(ts) FROM readings").fetchone()
h = con.execute("SELECT count(*) FROM history").fetchone()[0]
fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
print(f"    integrity ok — {n} readings ({fmt(lo)} .. {fmt(hi)}), {h} history rows")
PYEOF

echo "==> mode: $MODE"
echo "==> stopping the service on $REMOTE"
ssh "$REMOTE" "$REMOTE_SYSTEMCTL stop '$SERVICE' 2>/dev/null || true" 2>&1 | strip_ssh_noise
STOPPED=1

echo "==> snapshotting the current target DB (if any)"
ssh "$REMOTE" "cd '$APP_DIR' && if [ -f sensor.db ]; then for f in sensor.db sensor.db-wal sensor.db-shm; do [ -f \"\$f\" ] && cp -a \"\$f\" \"\$f.pre-restore-$STAMP\"; done; echo '    saved as sensor.db*.pre-restore-$STAMP'; else echo '    no existing sensor.db on the target'; fi" 2>&1 | strip_ssh_noise

echo "==> uploading the backup"
scp -q "$SRC" "$REMOTE:$APP_DIR/sensor.db.incoming"

if [ "$MODE" = "replace" ]; then
  echo "==> replacing sensor.db (stale -wal/-shm removed so SQLite can't mix old and new)"
  ssh "$REMOTE" "cd '$APP_DIR' && rm -f sensor.db sensor.db-wal sensor.db-shm && mv sensor.db.incoming sensor.db" 2>&1 | strip_ssh_noise
else
  echo "==> merging into sensor.db"
  scp -q "$ROOT/deploy/merge-db.py" "$REMOTE:$APP_DIR/merge-db.py"
  ssh "$REMOTE" "cd '$APP_DIR' && { [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=python3; } && \$PY merge-db.py sensor.db sensor.db.incoming && rm -f sensor.db.incoming merge-db.py" 2>&1 | strip_ssh_noise
fi

echo "==> starting the service"
ssh "$REMOTE" "$REMOTE_SYSTEMCTL start '$SERVICE'" 2>&1 | strip_ssh_noise
DONE=1

echo "==> verifying through the API"
HEALTH_PY='import json,urllib.request,datetime as dt;d=json.load(urllib.request.urlopen("http://127.0.0.1:8787/api/history?range=all",timeout=10));e=d["extent"];f=lambda t: dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "none";print("    dashboard data spans",f(e["earliest"]),"..",f(e["latest"]),"in",len(d["series"]),"buckets")'
sleep 3
ssh "$REMOTE" "cd '$APP_DIR' && .venv/bin/python -c '$HEALTH_PY'" 2>&1 | strip_ssh_noise || \
  echo "!!! API check failed — inspect: ssh $REMOTE \"tail -n 40 '$APP_DIR/app.log'\""

echo
echo "Done. Dashboard: http://${REMOTE#*@}:8787"
echo "Pre-restore snapshot kept on the target as sensor.db*.pre-restore-$STAMP (delete when happy)."
