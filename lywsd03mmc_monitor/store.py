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


NICE_BUCKETS = [120, 300, 600, 1800, 3600, 10800, 21600, 43200, 86400]


def pick_bucket(span_seconds: int, target: int = 360) -> int:
    """Smallest 'nice' bucket width that keeps a span under ~target points.
    The 120 s floor tolerates the sensor's staggered per-field advertisements
    (a bucket almost always catches at least one packet per field)."""
    for b in NICE_BUCKETS:
        if span_seconds / b <= target:
            return b
    return NICE_BUCKETS[-1]


def _gaps_longer_than_one_hour(hours: list[int]) -> list[int]:
    """Keep only hours that belong to a run of >= 2 consecutive empty hour-buckets
    (a gap longer than one hour). Isolated single-hour holes are dropped."""
    if not hours:
        return []
    hours = sorted(hours)
    keep, run = [], [hours[0]]
    for h in hours[1:]:
        if h == run[-1] + 3600:
            run.append(h)
        else:
            if len(run) >= 2:
                keep.extend(run)
            run = [h]
    if len(run) >= 2:
        keep.extend(run)
    return keep


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
        # Only a contiguous stretch of MORE THAN ONE missing hour counts as a real,
        # fillable gap; isolated single-hour holes are ignored (the chart just
        # interpolates across them).
        gaps = _gaps_longer_than_one_hour(self.fillable_gaps(start, end))
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

    def aggregated_series(self, start: int, end: int, bucket: int) -> list[dict]:
        """One entry per bucket start covering [start, end], nulls where no data.
        Live readings win per field per bucket; hours known only from the device's
        hourly history fill the rest (value = hour midpoint, band = min..max).
        A bucket with neither source keeps nulls so the chart breaks the line."""
        first = start - (start % bucket)
        read = {}
        for r in self.conn.execute(
            "SELECT (ts/?)*? AS b, AVG(temperature), MIN(temperature), MAX(temperature), "
            "AVG(humidity), MIN(humidity), MAX(humidity) FROM readings "
            "WHERE ts >= ? AND ts <= ? GROUP BY b", (bucket, bucket, first, end),
        ):
            read[r[0]] = r[1:]
        hist = {}
        for h in self.conn.execute(
            "SELECT hour_ts, min_temp, max_temp, min_hum, max_hum FROM history "
            "WHERE hour_ts >= ? AND hour_ts <= ?", (first - 3600, end),
        ):
            hist[h[0]] = h[1:]
        hist_b = {}
        if bucket >= 3600:
            for hour_ts, (mnt, mxt, mnh, mxh) in hist.items():
                b = hour_ts - (hour_ts % bucket)
                acc = hist_b.setdefault(b, {"t": [], "tn": [], "tx": [],
                                            "h": [], "hn": [], "hx": []})
                if mnt is not None and mxt is not None:
                    acc["t"].append((mnt + mxt) / 2)
                    acc["tn"].append(mnt)
                    acc["tx"].append(mxt)
                if mnh is not None and mxh is not None:
                    acc["h"].append((mnh + mxh) / 2)
                    acc["hn"].append(mnh)
                    acc["hx"].append(mxh)
        out = []
        b = first
        while b <= end:
            t = tn = tx = hu = hn = hx = None
            if b in read:
                t, tn, tx, hu, hn, hx = read[b]
            if bucket >= 3600:
                acc = hist_b.get(b)
                if acc:
                    if t is None and acc["t"]:
                        t = sum(acc["t"]) / len(acc["t"])
                        tn, tx = min(acc["tn"]), max(acc["tx"])
                    if hu is None and acc["h"]:
                        hu = sum(acc["h"]) / len(acc["h"])
                        hn, hx = min(acc["hn"]), max(acc["hx"])
            else:
                row = hist.get(b - (b % 3600))
                if row:
                    mnt, mxt, mnh, mxh = row
                    if t is None and mnt is not None and mxt is not None:
                        t, tn, tx = (mnt + mxt) / 2, mnt, mxt
                    if hu is None and mnh is not None and mxh is not None:
                        hu, hn, hx = (mnh + mxh) / 2, mnh, mxh
            out.append({"t": b, "temp": t, "temp_min": tn, "temp_max": tx,
                        "hum": hu, "hum_min": hn, "hum_max": hx})
            b += bucket
        return out

    def history_window(self, start: int, end: int) -> dict:
        # Return raw per-minute readings + the device's hourly history for the window. The
        # frontend holds this in memory and downsamples for display, so it can pan/zoom
        # client-side without re-fetching. Overlap (an hour with both) is deduped in the
        # frontend merge (live readings win per hour). Volume is bounded by the window width.
        return {"points": self.readings_between(start, end),
                "bands": self.history_between(start, end)}
