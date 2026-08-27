"""Merge a backed-up sensor.db into the live one. Runs ON the target, in the app dir.

    python merge-db.py <target.db> <incoming.db>

Readings and history are keyed by timestamp (`ts` / `hour_ts` primary keys), so
INSERT OR IGNORE makes this idempotent and non-destructive: rows the live DB
already has win, everything else is added. Re-running it changes nothing.
"""
import sqlite3
import sys
from pathlib import Path

# On the target this script sits next to the package (sys.path[0] covers it); the
# extra entry lets it also run in place from deploy/ in a checkout.
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))

from lywsd03mmc_monitor.store import Store  # noqa: E402

TABLES = ("readings", "history", "meta")


def counts(con, schema="main"):
    return {t: con.execute(f"SELECT count(*) FROM {schema}.{t}").fetchone()[0] for t in TABLES}


def main() -> int:
    target, incoming = sys.argv[1], sys.argv[2]

    Store(target).conn.close()  # creates the file + schema if it doesn't exist yet

    con = sqlite3.connect(target)
    con.execute("PRAGMA busy_timeout=10000")
    before = counts(con)

    con.execute("ATTACH DATABASE ? AS src", (incoming,))
    present = {r[0] for r in con.execute(
        "SELECT name FROM src.sqlite_master WHERE type='table'")}
    missing = [t for t in TABLES if t not in present]
    if missing:
        print(f"    note: backup has no {', '.join(missing)} table — skipped")
    src_counts = counts(con, "src") if not missing else \
        {t: (con.execute(f"SELECT count(*) FROM src.{t}").fetchone()[0] if t in present else 0)
         for t in TABLES}

    for table in TABLES:
        if table in present:
            con.execute(f"INSERT OR IGNORE INTO {table} SELECT * FROM src.{table}")
    con.commit()
    after = counts(con)

    for table in TABLES:
        print(f"    {table:9s} live {before[table]:>7d}  + backup {src_counts[table]:>7d}"
              f"  -> {after[table]:>7d}  (added {after[table] - before[table]})")
    span = con.execute("SELECT min(ts), max(ts) FROM readings").fetchone()
    if span[0]:
        print(f"    readings span: {span[0]} .. {span[1]}")

    con.execute("DETACH DATABASE src")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
