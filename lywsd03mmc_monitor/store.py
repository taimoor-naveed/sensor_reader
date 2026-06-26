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
