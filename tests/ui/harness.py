import math
import os
import socket
import tempfile
import threading
import time

import uvicorn

from lywsd03mmc_monitor.protocol import Reading
from lywsd03mmc_monitor.state import LiveState
from lywsd03mmc_monitor.store import Store
from lywsd03mmc_monitor.web import create_app

NOW = 1_700_000_000  # fixed clock for deterministic tests


def _seed(store):
    # one reading per hour for the last week (drives the week view)
    for h in range(168, 0, -1):
        ts = NOW - h * 3600
        store.add_reading(ts, round(21 + 3 * math.sin(h / 6.0), 1),
                          round(45 + 8 * math.cos(h / 5.0)), 80, -58)
    # minute detail for the last 2 hours (drives hour/day views)
    for m in range(120, 0, -1):
        ts = NOW - m * 60
        store.add_reading(ts, round(21 + 2 * math.sin(m / 20.0), 1),
                          round(45 + 5 * math.cos(m / 18.0)), 80, -58)


def build_app(scenario="normal"):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(path)
    if scenario != "empty":
        _seed(store)
    if scenario == "with_gaps":
        # remove a 5-hour block ~40h ago to create a fillable gap
        for h in range(40, 45):
            store.conn.execute("DELETE FROM readings WHERE ts >= ? AND ts < ?",
                               (NOW - h * 3600, NOW - (h - 1) * 3600))
        store.conn.commit()

    state = LiveState(offline_after=150, battery_warn_below=15)
    if scenario != "empty":
        battery = 9 if scenario == "low_battery" else 80
        last_seen = NOW - 600 if scenario == "offline" else NOW
        state.update(Reading(temperature=21.4, humidity=46, battery=battery), -58, last_seen)

    if scenario != "empty":
        store.set_device_boot_epoch(NOW - 3 * 86400)   # ~3 days of fillable history

    def fake_backfill(start_ts, end_ts, progress_cb=None):
        n = 0
        h = start_ts - (start_ts % 3600)
        total = max(1, (end_ts - start_ts) // 3600)
        while h <= end_ts:
            store.add_history(h, 18.0, 22.0, 40, 55)
            n += 1
            if progress_cb:
                progress_cb(n, total)
            h += 3600
        return n

    def fake_update():
        return {"temperature": 22.9, "humidity": 48, "voltage": 3.0,
                "battery": 90, "boot_epoch": NOW - 3 * 86400}

    return create_app(store, state, now_fn=lambda: NOW,
                      backfill_fn=fake_backfill, update_fn=fake_update)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LiveServer:
    def __init__(self, app):
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)
