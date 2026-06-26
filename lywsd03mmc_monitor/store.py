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
