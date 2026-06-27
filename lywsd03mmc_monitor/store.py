import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  ts INTEGER PRIMARY KEY,
  temperature REAL,
  humidity REAL,
  battery INTEGER,
  rssi INTEGER
);
CREATE TABLE IF NOT EXISTS history (
  hour_ts INTEGER PRIMARY KEY,
  min_temp REAL,
  max_temp REAL,
  min_hum INTEGER,
  max_hum INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def add_reading(self, ts, temperature, humidity, battery, rssi) -> None:
        minute = ts - (ts % 60)
        self.conn.execute(
            "INSERT OR REPLACE INTO readings (ts, temperature, humidity, battery, rssi) "
            "VALUES (?, ?, ?, ?, ?)",
            (minute, temperature, humidity, battery, rssi),
        )
        self.conn.commit()

    def readings_between(self, start: int, end: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT ts, temperature, humidity, battery, rssi FROM readings "
            "WHERE ts >= ? AND ts <= ? ORDER BY ts",
            (start, end),
        )
        return [
            {"ts": r[0], "temperature": r[1], "humidity": r[2],
             "battery": r[3], "rssi": r[4]}
            for r in cur.fetchall()
        ]

    def hourly_rollup(self, start: int, end: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT (ts/3600)*3600 AS hour, MIN(temperature), MAX(temperature), "
            "MIN(humidity), MAX(humidity) FROM readings "
            "WHERE ts >= ? AND ts <= ? GROUP BY hour ORDER BY hour",
            (start, end),
        )
        return [
            {"hour_ts": r[0], "min_temp": r[1], "max_temp": r[2],
             "min_hum": r[3], "max_hum": r[4], "source": "live"}
            for r in cur.fetchall()
        ]

    def history_between(self, start: int, end: int) -> list[dict]:
        cur = self.conn.execute(
            "SELECT hour_ts, min_temp, max_temp, min_hum, max_hum FROM history "
            "WHERE hour_ts >= ? AND hour_ts <= ? ORDER BY hour_ts",
            (start, end),
        )
        return [
            {"hour_ts": r[0], "min_temp": r[1], "max_temp": r[2],
             "min_hum": r[3], "max_hum": r[4], "source": "device"}
            for r in cur.fetchall()
        ]

    def add_history(self, hour_ts, min_temp, max_temp, min_hum, max_hum) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO history (hour_ts, min_temp, max_temp, min_hum, max_hum) "
            "VALUES (?, ?, ?, ?, ?)",
            (hour_ts, min_temp, max_temp, min_hum, max_hum),
        )
        self.conn.commit()

    def fillable_gaps(self, start: int, end: int) -> list[int]:
        first_hour = start - (start % 3600)
        gaps = []
        h = first_hour
        while h < end:
            has_reading = self.conn.execute(
                "SELECT 1 FROM readings WHERE ts >= ? AND ts < ? LIMIT 1",
                (h, h + 3600),
            ).fetchone()
            has_history = self.conn.execute(
                "SELECT 1 FROM history WHERE hour_ts = ? LIMIT 1", (h,)
            ).fetchone()
            if not has_reading and not has_history:
                gaps.append(h)
            h += 3600
        return gaps

    def set_device_boot_epoch(self, epoch: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('device_boot_epoch', ?)",
            (str(int(epoch)),),
        )
        self.conn.commit()

    def get_device_boot_epoch(self):
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'device_boot_epoch'"
        ).fetchone()
        return int(row[0]) if row else None

    def classify_gaps(self, start: int, end: int, boot_epoch) -> dict:
        gaps = self.fillable_gaps(start, end)
        if boot_epoch is None:
            return {"fillable": gaps, "unrecoverable": []}
        # The device only logs COMPLETED hours, so its earliest record is the hour
        # AFTER boot; the partial boot hour itself is never recoverable. Hence the
        # boundary is strict (`>`), which avoids a perpetual off-by-one "1 fillable"
        # hour at the oldest edge of the device's memory.
        boot_hour = boot_epoch - (boot_epoch % 3600)
        fillable = [h for h in gaps if h > boot_hour]
        unrecoverable = [h for h in gaps if h <= boot_hour]
        return {"fillable": fillable, "unrecoverable": unrecoverable}

    def today_high_low(self, start: int, end: int) -> dict:
        union = (
            "SELECT ts AS t, temperature AS v FROM readings WHERE ts >= ? AND ts < ? "
            "UNION ALL SELECT hour_ts, max_temp FROM history WHERE hour_ts >= ? AND hour_ts < ? "
            "UNION ALL SELECT hour_ts, min_temp FROM history WHERE hour_ts >= ? AND hour_ts < ?"
        )
        args = (start, end, start, end, start, end)
        hi = self.conn.execute(
            f"SELECT t, v FROM ({union}) WHERE v IS NOT NULL ORDER BY v DESC, t ASC LIMIT 1", args
        ).fetchone()
        lo = self.conn.execute(
            f"SELECT t, v FROM ({union}) WHERE v IS NOT NULL ORDER BY v ASC, t ASC LIMIT 1", args
        ).fetchone()
        return {
            "high": hi[1] if hi else None, "high_ts": hi[0] if hi else None,
            "low": lo[1] if lo else None, "low_ts": lo[0] if lo else None,
        }

    def extent(self) -> dict:
        row = self.conn.execute(
            "SELECT MIN(t), MAX(t) FROM "
            "(SELECT ts AS t FROM readings UNION ALL SELECT hour_ts FROM history)"
        ).fetchone()
        return {"earliest": row[0], "latest": row[1]}

    def history_window(self, start: int, end: int) -> dict:
        if end - start <= 48 * 3600:
            return {"points": self.readings_between(start, end),
                    "bands": self.history_between(start, end)}
        bands = self.hourly_rollup(start, end) + self.history_between(start, end)
        bands.sort(key=lambda b: b["hour_ts"])
        return {"points": [], "bands": bands}
