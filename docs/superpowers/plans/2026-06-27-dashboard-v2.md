# Dashboard v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the LYWSD03MMC dashboard (Aurora theme) with battery-aware live updates, an on-demand Update action, progress-tracked history backfill, a 4-bar signal indicator, synced pan/zoom charts, and labeled gap handling.

**Architecture:** Keep the v1 layering — `protocol` (pure parsing), `state` (live snapshot), `store` (SQLite), `gatt` (on-demand BLE), `web` (FastAPI), `main` (wiring), `static/index.html` (single-page UI). v2 extends each: per-field freshness + battery source in state; boot-epoch persistence + gap classification + today/extent queries in store; `read_live` + progress backfill in gatt; new/generalized endpoints + a single-flight GATT guard in web; full Aurora rebuild of the frontend.

**Tech Stack:** Python 3.11+, FastAPI, bleak, SQLite, pytest, Playwright; Chart.js + chartjs-plugin-zoom + hammer.js (all vendored offline).

## Global Constraints

- **No persistent GATT connection.** GATT is used only by the two on-demand actions (Update, Fill), serialized single-flight.
- **No SSE/WebSocket.** Browser uses light polling (`/api/current` every 2 s).
- **Vendored assets only** — no CDN/external hosts (offline). New JS goes under `static/vendor/`.
- **Backwards-compatible call sites:** `LiveState.update(reading, rssi, now)` must keep working (new `source` param defaults to `"advert"`).
- **TDD:** failing test → confirm fail → implement → confirm pass → commit. Run `python -m pytest` from repo root (venv active).
- Spec: `docs/superpowers/specs/2026-06-27-dashboard-v2-design.md`. Approved mockups (markup/style source of truth): `.superpowers/brainstorm/44982-1782557608/content/aurora-dashboard-v4.html` and `backfill-gaps-flow.html`.

## File Structure

- `lywsd03mmc_monitor/protocol.py` — add DATA/NUMREC parsers, voltage→%, new UUIDs.
- `lywsd03mmc_monitor/state.py` — per-field timestamps, battery source, `rssi_to_bars`, extended snapshot.
- `lywsd03mmc_monitor/store.py` — `meta` table + boot-epoch get/set, `classify_gaps`, `today_high_low`, `extent`, `history_window`.
- `lywsd03mmc_monitor/gatt.py` — `read_live`, progress-reporting `backfill` (+ NUMREC, injectable client factory, boot-epoch persistence).
- `lywsd03mmc_monitor/web.py` — generalized `/api/history`, split `/api/gaps`, `/api/update`, async `/api/backfill` + `/api/backfill/status`, single-flight guard, today/extent in responses.
- `lywsd03mmc_monitor/main.py` — wire `update_fn` and progress `backfill_fn`.
- `lywsd03mmc_monitor/static/index.html` — Aurora rebuild (structure, live behaviors, charts, flows).
- `lywsd03mmc_monitor/static/vendor/` — add `chartjs-plugin-zoom.min.js`, `hammer.min.js`.
- `tests/test_protocol.py`, `test_state.py`, `test_store.py`, `test_gatt.py`, `test_web.py` — unit/API.
- `tests/ui/harness.py`, `tests/ui/test_dashboard.py`, `tests/ui/test_harness_smoke.py` — UI harness + tests.

---

## Task 1: Protocol — DATA/NUMREC parsers + voltage→%

**Files:**
- Modify: `lywsd03mmc_monitor/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `UUID_DATA = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"`, `UUID_NUMREC = "ebe0ccb9-7a0a-4b0c-8a1a-6ff2997da3a6"`
  - `parse_data(data: bytes) -> tuple[float, int, float]` → `(temperature_c, humidity_pct, voltage_v)` from `<hBh` (temp/100, humidity byte, voltage/1000)
  - `parse_numrec(data: bytes) -> tuple[int, int]` → `(total, current)` from `<II`
  - `voltage_to_percent(volts: float) -> int` → linear 2.1 V→0 %, 3.1 V→100 %, clamped 0–100

- [ ] **Step 1: Write the failing tests** — append to `tests/test_protocol.py`:

```python
from lywsd03mmc_monitor.protocol import parse_data, parse_numrec, voltage_to_percent
import struct


def test_parse_data_decodes_temp_humidity_voltage():
    raw = struct.pack("<hBh", 2340, 48, 3040)  # 23.40C, 48%, 3.040V
    temp, hum, volts = parse_data(raw)
    assert temp == 23.4
    assert hum == 48
    assert abs(volts - 3.04) < 1e-9


def test_parse_numrec_total_and_current():
    raw = struct.pack("<II", 54, 12)
    assert parse_numrec(raw) == (54, 12)


def test_voltage_to_percent_curve_and_clamp():
    assert voltage_to_percent(3.1) == 100
    assert voltage_to_percent(2.1) == 0
    assert voltage_to_percent(3.04) == 94
    assert voltage_to_percent(3.6) == 100   # clamp high
    assert voltage_to_percent(1.8) == 0     # clamp low
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_protocol.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement** — add to `lywsd03mmc_monitor/protocol.py` (near the other UUIDs/parsers):

```python
UUID_DATA = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_NUMREC = "ebe0ccb9-7a0a-4b0c-8a1a-6ff2997da3a6"


def parse_data(data: bytes) -> tuple[float, int, float]:
    temp_raw, humidity, volt_raw = struct.unpack("<hBh", data[:5])
    return temp_raw / 100, humidity, volt_raw / 1000


def parse_numrec(data: bytes) -> tuple[int, int]:
    total, current = struct.unpack("<II", data[:8])
    return total, current


def voltage_to_percent(volts: float) -> int:
    pct = (volts - 2.1) / (3.1 - 2.1) * 100
    return max(0, min(100, round(pct)))
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_protocol.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/protocol.py tests/test_protocol.py
git commit -m "feat(protocol): DATA/NUMREC parsers and voltage-to-percent"
```

---

## Task 2: State — per-field freshness, battery source, signal bars

**Files:**
- Modify: `lywsd03mmc_monitor/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `protocol.Reading`
- Produces:
  - `rssi_to_bars(rssi: int | None) -> int` (0–4)
  - `LiveState.update(reading, rssi, now, source="advert")`
  - New attrs `temperature_ts, humidity_ts, battery_ts` (float|None), `battery_source` (str|None)
  - `snapshot(now)` adds keys: `temperature_ts, humidity_ts, battery_ts, battery_source, signal_bars`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_state.py`:

```python
from lywsd03mmc_monitor.state import rssi_to_bars


def test_rssi_to_bars_thresholds():
    assert rssi_to_bars(-50) == 4
    assert rssi_to_bars(-60) == 3
    assert rssi_to_bars(-75) == 2
    assert rssi_to_bars(-85) == 1
    assert rssi_to_bars(-95) == 0
    assert rssi_to_bars(None) == 0


def test_per_field_timestamps_only_set_for_present_fields():
    st = LiveState(offline_after=150, battery_warn_below=15)
    st.update(Reading(temperature=20.0, humidity=40.0, battery=80), -55, 1000.0)
    st.update(Reading(humidity=42.0), -56, 1010.0)   # humidity-only advert
    snap = st.snapshot(1010.0)
    assert snap["humidity_ts"] == 1010.0
    assert snap["temperature_ts"] == 1000.0          # unchanged
    assert snap["battery_ts"] == 1000.0
    assert snap["signal_bars"] == rssi_to_bars(-56)


def test_battery_source_tracks_origin():
    st = LiveState(offline_after=150, battery_warn_below=15)
    st.update(Reading(battery=80), -55, 1000.0)              # passive
    assert st.snapshot(1000.0)["battery_source"] == "advert"
    st.update(Reading(battery=93), -55, 1100.0, source="gatt")
    assert st.snapshot(1100.0)["battery_source"] == "gatt"
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_state.py -q` → FAIL.

- [ ] **Step 3: Implement** — edit `lywsd03mmc_monitor/state.py`:

```python
def rssi_to_bars(rssi):
    if rssi is None:
        return 0
    if rssi >= -55:
        return 4
    if rssi >= -67:
        return 3
    if rssi >= -80:
        return 2
    if rssi >= -90:
        return 1
    return 0
```

In `__init__` add: `self.temperature_ts = self.humidity_ts = self.battery_ts = None` and `self.battery_source = None`.

Replace `update`:

```python
    def update(self, reading, rssi, now, source="advert") -> None:
        if reading.temperature is not None:
            self.temperature = reading.temperature
            self.temperature_ts = now
        if reading.humidity is not None:
            self.humidity = reading.humidity
            self.humidity_ts = now
        if reading.battery is not None:
            self.battery = reading.battery
            self.battery_ts = now
            self.battery_source = source
        self.rssi = rssi
        self.last_seen = now
```

In `snapshot`, add to the returned dict:

```python
            "temperature_ts": self.temperature_ts,
            "humidity_ts": self.humidity_ts,
            "battery_ts": self.battery_ts,
            "battery_source": self.battery_source,
            "signal_bars": rssi_to_bars(self.rssi),
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_state.py -q` → PASS (old tests still pass; `update` is backward compatible).

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/state.py tests/test_state.py
git commit -m "feat(state): per-field freshness, battery source, signal bars"
```

---

## Task 3: Store — boot epoch persistence + gap classification

**Files:**
- Modify: `lywsd03mmc_monitor/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  - `Store.set_device_boot_epoch(epoch: int) -> None`
  - `Store.get_device_boot_epoch() -> int | None`
  - `Store.classify_gaps(start, end, boot_epoch) -> dict` → `{"fillable": [hour_ts...], "unrecoverable": [hour_ts...]}`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_store.py`:

```python
def test_boot_epoch_round_trips(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.get_device_boot_epoch() is None
    s.set_device_boot_epoch(1_700_000_000)
    assert s.get_device_boot_epoch() == 1_700_000_000
    s.set_device_boot_epoch(1_700_000_500)   # replace
    assert s.get_device_boot_epoch() == 1_700_000_500


def test_classify_gaps_splits_on_boot_epoch(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    # hours 0..4 are all empty -> all gaps; boot at hour 2
    res = s.classify_gaps(0, 5 * 3600, boot_epoch=2 * 3600)
    assert res["unrecoverable"] == [0, 3600]
    assert res["fillable"] == [7200, 10800, 14400]


def test_classify_gaps_none_boot_all_fillable(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    res = s.classify_gaps(0, 2 * 3600, boot_epoch=None)
    assert res["unrecoverable"] == []
    assert res["fillable"] == [0, 3600]
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_store.py -q` → FAIL.

- [ ] **Step 3: Implement** — in `store.py`, extend `_SCHEMA` with:

```sql
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

Add methods:

```python
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
        boot_hour = boot_epoch - (boot_epoch % 3600)
        fillable = [h for h in gaps if h >= boot_hour]
        unrecoverable = [h for h in gaps if h < boot_hour]
        return {"fillable": fillable, "unrecoverable": unrecoverable}
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_store.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/store.py tests/test_store.py
git commit -m "feat(store): boot-epoch persistence and fillable/unrecoverable gap split"
```

---

## Task 4: Store — today high/low, extent, history window

**Files:**
- Modify: `lywsd03mmc_monitor/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  - `Store.today_high_low(start, end) -> dict` → `{"high","low","high_ts","low_ts"}` (None if no data) over readings + history
  - `Store.extent() -> dict` → `{"earliest","latest"}` (None if empty)
  - `Store.history_window(start, end) -> dict` → `{"points":[...], "bands":[...]}` (raw if span ≤ 48 h, else hourly bands)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_store.py`:

```python
def test_today_high_low_picks_extremes_with_ts(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(100, 20.0, 40.0, 90, -60)
    s.add_reading(200, 25.5, 41.0, 90, -60)   # high
    s.add_reading(300, 18.2, 39.0, 90, -60)   # low
    res = s.today_high_low(0, 1000)
    assert res["high"] == 25.5 and res["high_ts"] == 200
    assert res["low"] == 18.2 and res["low_ts"] == 300


def test_today_high_low_empty(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.today_high_low(0, 1000) == {"high": None, "low": None,
                                         "high_ts": None, "low_ts": None}


def test_extent_spans_readings_and_history(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(5000, 20.0, 40.0, 90, -60)
    s.add_history(3600, 18.0, 22.0, 40, 55)   # earlier
    ext = s.extent()
    assert ext["earliest"] == 3600
    assert ext["latest"] == 4980   # 5000 floored to minute


def test_history_window_raw_vs_bands(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(1000, 20.0, 40.0, 90, -60)
    raw = s.history_window(0, 3600)            # 1h span -> raw points
    assert raw["points"] and raw["bands"] == []
    s.add_reading(100, 19.0, 39.0, 90, -60)
    s.add_reading(200, 23.0, 44.0, 90, -60)
    wide = s.history_window(0, 3 * 86400)      # 3d span -> bands only
    assert wide["points"] == []
    assert any(b["min_temp"] == 19.0 and b["max_temp"] == 23.0 for b in wide["bands"])
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_store.py -q` → FAIL.

- [ ] **Step 3: Implement** — add to `store.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_store.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/store.py tests/test_store.py
git commit -m "feat(store): today high/low, data extent, history window granularity"
```

---

## Task 5: GATT — `read_live` (Update action)

**Files:**
- Modify: `lywsd03mmc_monitor/gatt.py`
- Test: `tests/test_gatt.py`

**Interfaces:**
- Consumes: `protocol.parse_data`, `protocol.parse_device_time`, `protocol.voltage_to_percent`, `device_start_epoch`, `UUID_DATA`, `UUID_TIME`
- Produces: `async read_live(device, client_factory=None) -> dict` → `{"temperature","humidity","voltage","battery","boot_epoch"}`. Raises `RuntimeError` if `device is None`. `client_factory(device)` returns an async-context-manager BLE client (defaults to `bleak.BleakClient`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_gatt.py`:

```python
import struct
import asyncio
import pytest
from lywsd03mmc_monitor import protocol


class _FakeClient:
    def __init__(self, chars):
        self._chars = chars
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def read_gatt_char(self, uuid):
        return self._chars[uuid]


def test_read_live_returns_reading_and_boot_epoch(monkeypatch):
    from lywsd03mmc_monitor.gatt import read_live, UUID_TIME
    from lywsd03mmc_monitor.protocol import UUID_DATA
    chars = {
        UUID_DATA: struct.pack("<hBh", 2310, 47, 3010),   # 23.10C, 47%, 3.010V
        UUID_TIME: struct.pack("<I", 7200),               # booted 7200s ago
    }
    monkeypatch.setattr("time.time", lambda: 1_700_000_000)
    res = asyncio.run(read_live(object(), client_factory=lambda d: _FakeClient(chars)))
    assert res["temperature"] == 23.1
    assert res["humidity"] == 47
    assert res["battery"] == protocol.voltage_to_percent(3.010)
    assert res["boot_epoch"] == 1_700_000_000 - 7200


def test_read_live_requires_device():
    from lywsd03mmc_monitor.gatt import read_live
    with pytest.raises(RuntimeError):
        asyncio.run(read_live(None))
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_gatt.py -q` → FAIL.

- [ ] **Step 3: Implement** — add to `gatt.py` (import `parse_data`, `voltage_to_percent`, `UUID_DATA` from protocol; keep `UUID_TIME`):

```python
def _default_client_factory(device):
    from bleak import BleakClient
    return BleakClient(device)


async def read_live(device, client_factory=None) -> dict:
    if device is None:
        raise RuntimeError("sensor not yet seen; wait for an advertisement first")
    client_factory = client_factory or _default_client_factory
    async with client_factory(device) as client:
        data_raw = await client.read_gatt_char(protocol.UUID_DATA)
        time_raw = await client.read_gatt_char(UUID_TIME)
    temp, humidity, volts = protocol.parse_data(bytes(data_raw))
    device_clock, _tz = protocol.parse_device_time(bytes(time_raw))
    boot_epoch = device_start_epoch(device_clock, int(time.time()))
    return {
        "temperature": temp, "humidity": humidity, "voltage": volts,
        "battery": protocol.voltage_to_percent(volts), "boot_epoch": boot_epoch,
    }
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_gatt.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/gatt.py tests/test_gatt.py
git commit -m "feat(gatt): read_live one-shot DATA+TIME reading with battery and boot epoch"
```

---

## Task 6: GATT — backfill progress + NUMREC + boot-epoch persistence

**Files:**
- Modify: `lywsd03mmc_monitor/gatt.py`
- Test: `tests/test_gatt.py`

**Interfaces:**
- Produces: `async backfill(device, store, start_ts, end_ts, progress_cb=None, timeout=20.0, poll=2.0, retries=2, client_factory=None) -> int`. Calls `progress_cb(received, total)` as records arrive (`total` from NUMREC), persists `store.set_device_boot_epoch(base)`, returns rows written in range. Uses the injectable `client_factory` and a notify-capable fake in tests.

- [ ] **Step 1: Write the failing test** — append to `tests/test_gatt.py`:

```python
class _FakeNotifyClient:
    def __init__(self, *, time_raw, numrec_raw, records):
        self._time = time_raw
        self._numrec = numrec_raw
        self._records = records
        self._cb = None
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def read_gatt_char(self, uuid):
        from lywsd03mmc_monitor.gatt import UUID_TIME
        from lywsd03mmc_monitor.protocol import UUID_NUMREC
        return {UUID_TIME: self._time, UUID_NUMREC: self._numrec}[uuid]
    async def start_notify(self, uuid, cb):
        for rec in self._records:
            cb(0, rec)
    async def stop_notify(self, uuid):
        pass


def test_backfill_reports_progress_and_persists_boot(tmp_path, monkeypatch):
    from lywsd03mmc_monitor.gatt import backfill
    from lywsd03mmc_monitor.store import Store
    s = Store(str(tmp_path / "t.db"))
    host_now = 1_700_000_000
    monkeypatch.setattr("time.time", lambda: host_now)
    # two records 1h and 2h into uptime; device booted 3h ago
    recs = [struct.pack("<IIhBhB", i, off, 220, 55, 180, 40)
            for i, off in [(0, 3600), (1, 7200)]]
    client = _FakeNotifyClient(time_raw=struct.pack("<I", 3 * 3600),
                               numrec_raw=struct.pack("<II", 2, 2), records=recs)
    seen = []
    written = asyncio.run(backfill(
        object(), s, 0, host_now, progress_cb=lambda r, t: seen.append((r, t)),
        timeout=0.01, poll=0.01, client_factory=lambda d: client))
    assert written == 2
    assert seen[-1] == (2, 2)            # received == total
    assert s.get_device_boot_epoch() == host_now - 3 * 3600
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_gatt.py -q` → FAIL.

- [ ] **Step 3: Implement** — replace the body of `backfill` in `gatt.py`:

```python
async def backfill(device, store, start_ts, end_ts, progress_cb=None,
                   timeout=20.0, poll=2.0, retries=2, client_factory=None) -> int:
    if device is None:
        raise RuntimeError("sensor not yet seen; wait for an advertisement first")
    client_factory = client_factory or _default_client_factory

    records = []
    total = 0

    def on_history(_handle, data: bytes):
        try:
            records.append(protocol.parse_history_record(bytes(data)))
            if progress_cb:
                progress_cb(len(records), total)
        except Exception:
            pass

    base = None
    for attempt in range(retries + 1):
        records.clear()
        try:
            async with client_factory(device) as client:
                time_raw = await client.read_gatt_char(UUID_TIME)
                device_clock, _tz = protocol.parse_device_time(bytes(time_raw))
                base = device_start_epoch(device_clock, int(time.time()))
                try:
                    numrec_raw = await client.read_gatt_char(protocol.UUID_NUMREC)
                    total = protocol.parse_numrec(bytes(numrec_raw))[0]
                except Exception:
                    total = 0
                await client.start_notify(UUID_HISTORY, on_history)
                try:
                    while True:
                        before = len(records)
                        await asyncio.sleep(timeout if before == 0 else poll)
                        if len(records) == before:
                            break
                finally:
                    await client.stop_notify(UUID_HISTORY)
            break
        except Exception:
            if attempt == retries:
                raise

    if base is not None:
        store.set_device_boot_epoch(base)
    rows = records_to_rows(records, base)
    written = 0
    for hour_ts, mn_t, mx_t, mn_h, mx_h in rows:
        if start_ts <= hour_ts <= end_ts:
            store.add_history(hour_ts, mn_t, mx_t, mn_h, mx_h)
            written += 1
    return written
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_gatt.py -q` → PASS (existing `records_to_rows`/`device_start_epoch` tests still pass).

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/gatt.py tests/test_gatt.py
git commit -m "feat(gatt): backfill progress callback, NUMREC total, boot-epoch persist, injectable client"
```

---

## Task 7: Web — generalized `/api/history` with extent

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `store.history_window`, `store.extent`
- Produces: `GET /api/history` accepts either `range=hour|day|week` (back-compat) or `from=&to=`. Returns `{"range", "points", "bands", "extent": {"earliest","latest"}}`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_web.py`:

```python
def test_history_from_to_window_and_extent(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 600, 20.0, 40.0, 90, -60)
    r = client.get(f"/api/history?from={now-3600}&to={now}").json()
    assert any(p["temperature"] == 20.0 for p in r["points"])
    assert r["extent"]["earliest"] is not None
    assert r["extent"]["latest"] is not None
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_web.py::test_history_from_to_window_and_extent -q` → FAIL.

- [ ] **Step 3: Implement** — replace the `history` handler in `web.py`:

```python
    @app.get("/api/history")
    def history(range: str = None, **kwargs):
        from fastapi import Request  # noqa: F401 (params parsed below)
        now = int(now_fn())
        frm = kwargs.get("from")
        to = kwargs.get("to")
        # FastAPI passes query params named from/to via the function signature below
        return _history_payload(store, now, range, frm, to)
```

Because `from` is a Python keyword, declare params explicitly. Replace the above with this exact handler instead:

```python
    from fastapi import Query

    @app.get("/api/history")
    def history(range: str = None,
                start: int = Query(None, alias="from"),
                end: int = Query(None, alias="to")):
        now = int(now_fn())
        if start is None or end is None:
            span = _RANGES.get(range or "day", 86400)
            start, end = now - span, now
        win = store.history_window(int(start), int(end))
        return {"range": range or "custom", "points": win["points"],
                "bands": win["bands"], "extent": store.extent()}
```

(Delete the old `_RANGES`-based body; keep the `_RANGES` dict.)

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_web.py -q` → PASS (existing `range=day`/`range=week` tests still pass: day ≤ 48 h → points; week → bands).

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/web.py tests/test_web.py
git commit -m "feat(web): /api/history accepts from/to window and returns data extent"
```

---

## Task 8: Web — split `/api/gaps`

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py`, `tests/ui/test_harness_smoke.py`

**Interfaces:**
- Consumes: `store.classify_gaps`, `store.get_device_boot_epoch`
- Produces: `GET /api/gaps` (accepts `range` or `from/to`) → `{"fillable": [...], "unrecoverable": [...]}`.

- [ ] **Step 1: Update the existing tests** — in `tests/test_web.py` replace `test_gaps_endpoint`:

```python
def test_gaps_endpoint_returns_split(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 30, 20.0, 40.0, 90, -60)
    r = client.get("/api/gaps?range=hour").json()
    assert "fillable" in r and "unrecoverable" in r
```

In `tests/ui/test_harness_smoke.py` replace `test_with_gaps_reports_gaps`:

```python
def test_with_gaps_reports_gaps():
    with LiveServer(build_app("with_gaps")) as srv:
        r = httpx.get(srv.url + "/api/gaps?range=week")
        assert len(r.json()["fillable"]) >= 5
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_web.py::test_gaps_endpoint_returns_split -q` → FAIL.

- [ ] **Step 3: Implement** — replace the `gaps` handler in `web.py`:

```python
    @app.get("/api/gaps")
    def gaps(range: str = None,
             start: int = Query(None, alias="from"),
             end: int = Query(None, alias="to")):
        now = int(now_fn())
        if start is None or end is None:
            span = _RANGES.get(range or "week", 604800)
            start, end = now - span, now
        boot = store.get_device_boot_epoch()
        return store.classify_gaps(int(start), int(end), boot)
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_web.py -q tests/ui/test_harness_smoke.py -q` → PASS. (Harness `build_app` is updated in Task 18; if smoke fails on signature now, run it after Task 18.)

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/web.py tests/test_web.py tests/ui/test_harness_smoke.py
git commit -m "feat(web): /api/gaps returns fillable vs unrecoverable split"
```

---

## Task 9: Web — `/api/update` with single-flight guard

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: injected `update_fn() -> dict|awaitable` returning `{"temperature","humidity","voltage","battery","boot_epoch"}`
- Produces: `create_app(store, state, now_fn, backfill_fn=None, update_fn=None)`; `POST /api/update` applies the reading to state+store, persists boot epoch, returns the new snapshot. `503` if no `update_fn`; `409` if a GATT action is in flight (`app.state.gatt_busy`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_web.py`:

```python
def test_update_applies_reading_and_returns_snapshot(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(150, 15)
    def fake_update():
        return {"temperature": 22.2, "humidity": 50, "voltage": 3.0,
                "battery": 90, "boot_epoch": 1_699_990_000}
    app = create_app(store, state, now_fn=lambda: 1_700_000_000, update_fn=fake_update)
    client = TestClient(app)
    r = client.post("/api/update")
    body = r.json()
    assert body["temperature"] == 22.2
    assert body["battery"] == 90
    assert body["battery_source"] == "gatt"
    assert store.get_device_boot_epoch() == 1_699_990_000
    assert any(row["temperature"] == 22.2 for row in store.readings_between(0, 2_000_000_000))


def test_update_503_without_fn(tmp_path):
    store, state, client = _client(tmp_path, now=1000)
    assert client.post("/api/update").status_code == 503


def test_update_409_when_busy(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(150, 15)
    app = create_app(store, state, now_fn=lambda: 1000, update_fn=lambda: {})
    app.state.gatt_busy = True
    client = TestClient(app)
    assert client.post("/api/update").status_code == 409
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_web.py -k update -q` → FAIL.

- [ ] **Step 3: Implement** — in `web.py`: change `create_app` signature to `def create_app(store, state, now_fn, backfill_fn=None, update_fn=None)`; near the top of the function add `app.state.gatt_busy = False`; import `Reading`:

```python
    from lywsd03mmc_monitor.protocol import Reading

    @app.post("/api/update")
    async def do_update():
        if update_fn is None:
            raise HTTPException(status_code=503, detail="update unavailable")
        if app.state.gatt_busy:
            raise HTTPException(status_code=409, detail="sensor busy")
        app.state.gatt_busy = True
        try:
            result = update_fn()
            if hasattr(result, "__await__"):
                result = await result
            now = now_fn()
            state.update(
                Reading(temperature=result.get("temperature"),
                        humidity=result.get("humidity"),
                        battery=result.get("battery")),
                state.rssi, now, source="gatt")
            store.add_reading(int(now), result.get("temperature"),
                              result.get("humidity"), result.get("battery"), state.rssi)
            if result.get("boot_epoch") is not None:
                store.set_device_boot_epoch(int(result["boot_epoch"]))
            return JSONResponse(state.snapshot(now))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"update failed: {e}")
        finally:
            app.state.gatt_busy = False
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_web.py -k update -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/web.py tests/test_web.py
git commit -m "feat(web): POST /api/update one-shot reading with single-flight guard"
```

---

## Task 10: Web — async `/api/backfill` + `/api/backfill/status`

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: injected `backfill_fn(start, end, progress_cb) -> int|awaitable`
- Produces:
  - `POST /api/backfill` `{from,to}` → `{"started": True}`; sets `app.state.backfill_status` to `connecting`; `503`/`409` as for update.
  - `GET /api/backfill/status` → `{"state","received","total","filled","error"}` (`state ∈ idle|connecting|reading|done|error`).

- [ ] **Step 1: Rewrite the existing backfill test** — in `tests/test_web.py` replace `test_backfill_endpoint_calls_injected_fn`:

```python
def test_backfill_async_reports_status(tmp_path):
    import time as _t
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(150, 15)
    def fake_backfill(start, end, progress):
        progress(1, 3); progress(2, 3); progress(3, 3)
        return 3
    app = create_app(store, state, now_fn=lambda: 1000, backfill_fn=fake_backfill)
    client = TestClient(app)
    assert client.post("/api/backfill", json={"from": 0, "to": 3600}).json() == {"started": True}
    # poll status until done
    for _ in range(50):
        st = client.get("/api/backfill/status").json()
        if st["state"] == "done":
            break
        _t.sleep(0.02)
    assert st["state"] == "done"
    assert st["filled"] == 3
    assert st["total"] == 3


def test_backfill_503_without_fn(tmp_path):
    store, state, client = _client(tmp_path, now=1000)
    assert client.post("/api/backfill", json={"from": 0, "to": 1}).status_code == 503
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_web.py -k backfill -q` → FAIL.

- [ ] **Step 3: Implement** — in `web.py` add `import asyncio` at top; initialize `app.state.backfill_status = {"state": "idle", "received": 0, "total": 0, "filled": 0, "error": None}` near `gatt_busy`; replace the old `do_backfill` handler:

```python
    @app.post("/api/backfill")
    async def do_backfill(payload: dict = Body(...)):
        if backfill_fn is None:
            raise HTTPException(status_code=503, detail="backfill unavailable")
        if app.state.gatt_busy:
            raise HTTPException(status_code=409, detail="sensor busy")
        app.state.gatt_busy = True
        app.state.backfill_status = {"state": "connecting", "received": 0,
                                     "total": 0, "filled": 0, "error": None}
        start, end = int(payload["from"]), int(payload["to"])

        async def run():
            try:
                def progress(received, total):
                    app.state.backfill_status.update(
                        state="reading", received=received, total=total)
                result = backfill_fn(start, end, progress)
                if hasattr(result, "__await__"):
                    result = await result
                app.state.backfill_status.update(state="done", filled=int(result))
            except Exception as e:
                app.state.backfill_status.update(state="error", error=str(e))
            finally:
                app.state.gatt_busy = False

        asyncio.create_task(run())
        return {"started": True}

    @app.get("/api/backfill/status")
    def backfill_status():
        return app.state.backfill_status
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_web.py -k backfill -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/web.py tests/test_web.py
git commit -m "feat(web): async backfill with pollable progress status"
```

---

## Task 11: Web — `/api/current` adds today high/low

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `store.today_high_low`
- Produces: `/api/current` response = snapshot + `today_high, today_low, today_high_ts, today_low_ts` for the local day containing `now`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_web.py`:

```python
def test_current_includes_today_high_low(tmp_path):
    now = 1_700_000_000
    store, state, client = _client(tmp_path, now=now)
    state.update(Reading(temperature=21.0, humidity=44.0, battery=88), -52, now)
    store.add_reading(now - 300, 19.0, 40.0, 88, -52)
    store.add_reading(now - 200, 26.0, 41.0, 88, -52)
    r = client.get("/api/current").json()
    assert r["today_high"] == 26.0
    assert r["today_low"] == 19.0
    assert "signal_bars" in r
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_web.py::test_current_includes_today_high_low -q` → FAIL.

- [ ] **Step 3: Implement** — replace the `current` handler in `web.py` (add `from datetime import datetime` at top):

```python
    @app.get("/api/current")
    def current():
        now = now_fn()
        snap = state.snapshot(now)
        day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = int(day.timestamp())
        snap_today = store.today_high_low(day_start, day_start + 86400)
        snap.update({
            "today_high": snap_today["high"], "today_low": snap_today["low"],
            "today_high_ts": snap_today["high_ts"], "today_low_ts": snap_today["low_ts"],
        })
        return JSONResponse(snap)
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_web.py -q` → PASS (whole web suite).

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/web.py tests/test_web.py
git commit -m "feat(web): /api/current includes today high/low"
```

---

## Task 12: Main — wire update + progress backfill

**Files:**
- Modify: `lywsd03mmc_monitor/main.py`

**Interfaces:**
- Consumes: `gatt.read_live`, `gatt.backfill` (progress), `create_app(..., update_fn=...)`

- [ ] **Step 1: Implement** — in `main.py`, replace the `backfill_fn`/`create_app` block:

```python
    def backfill_fn(start_ts: int, end_ts: int, progress_cb=None):
        from lywsd03mmc_monitor.gatt import backfill
        return backfill(scanner.last_device, store, start_ts, end_ts, progress_cb=progress_cb)

    def update_fn():
        from lywsd03mmc_monitor.gatt import read_live
        return read_live(scanner.last_device)

    app = create_app(store, state, now_fn=time.time,
                     backfill_fn=backfill_fn, update_fn=update_fn)
```

- [ ] **Step 2: Verify the app imports & boots** — `python -c "import lywsd03mmc_monitor.main"` → no error. Run the whole backend suite: `python -m pytest tests/test_protocol.py tests/test_state.py tests/test_store.py tests/test_gatt.py tests/test_web.py -q` → PASS.

- [ ] **Step 3: Commit**

```bash
git add lywsd03mmc_monitor/main.py
git commit -m "feat(main): wire update_fn and progress-aware backfill_fn"
```

---

## Task 13: Vendor chart pan/zoom libraries

**Files:**
- Create: `lywsd03mmc_monitor/static/vendor/chartjs-plugin-zoom.min.js`
- Create: `lywsd03mmc_monitor/static/vendor/hammer.min.js`

- [ ] **Step 1: Fetch the libraries** (versions compatible with the vendored Chart.js v4):

```bash
cd lywsd03mmc_monitor/static/vendor
curl -fsSL https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js -o hammer.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js -o chartjs-plugin-zoom.min.js
test -s hammer.min.js && test -s chartjs-plugin-zoom.min.js && echo OK
```

If network is unavailable in the execution environment, vendor them from the local npm cache / a known-good copy instead; the only requirement is the two files exist and load offline. Confirm Chart.js version first: `grep -o 'Chart.js v[0-9.]*' chart.umd.min.js | head -1`.

- [ ] **Step 2: Verify load order note** — these must be included in `index.html` AFTER `chart.umd.min.js` and `hammer.min.js` before the zoom plugin (Task 16 handles the tags).

- [ ] **Step 3: Commit**

```bash
git add lywsd03mmc_monitor/static/vendor/hammer.min.js lywsd03mmc_monitor/static/vendor/chartjs-plugin-zoom.min.js
git commit -m "chore(vendor): add hammer.js and chartjs-plugin-zoom for chart interaction"
```

---

## Task 14: Frontend — Aurora structure & styles

**Files:**
- Modify: `lywsd03mmc_monitor/static/index.html`

**DOM contract (IDs/classes later tasks + UI tests depend on):**
- Header: `#signalBars` (container of 4 `<i>` bars), `#liveBadge` (text "Live"/"Offline") with `#liveDot`, `#updateBtn`.
- Tiles: `#temp`, `#tempUpdated`, `#comfort`; `#hum`, `#humUpdated`; `#bat`, `#batSub`, `#batUpdated`, `#batbadge`; `#dew`, `#abs`; `#todayHigh`, `#todayLow`, `#todaySub`.
- Gap banner: `#gapWrap`, `#gapFillable` (text), `#fillBtn`, `#gapUnrecoverable` (calm note), `#fillProgressWrap`, `#fillProgressBar`, `#fillProgressText`.
- Charts: range chips in `#ranges` with `button[data-r="6h|24h|7d|all"]`; `#tempChart`, `#humChart` canvases.

- [ ] **Step 1: Port markup + Aurora CSS** — Rebuild `index.html` body and `<style>` from the approved mockup `.superpowers/brainstorm/44982-1782557608/content/aurora-dashboard-v4.html` (header, hero, tiles, charts) plus the gap banner + progress markup from `backfill-gaps-flow.html`. Use the exact IDs/classes in the DOM contract above. Keep the v1 responsive rules (`@media` breakpoints, `overflow-x:hidden`, `min-height:44px` tap targets). Use Aurora tokens from spec §7. Replace the `<head>` script tags with (order matters):

```html
<script src="/static/vendor/chart.umd.min.js"></script>
<script src="/static/vendor/hammer.min.js"></script>
<script src="/static/vendor/chartjs-plugin-zoom.min.js"></script>
```

Add a `@keyframes pulse` and a `.pulse` class for the value-changed animation:

```css
@keyframes pulse { 0%{transform:scale(1)} 35%{transform:scale(1.06);filter:brightness(1.25)} 100%{transform:scale(1)} }
.pulse { animation: pulse .5s ease-out }
.bars i { width:4px; border-radius:1px; display:block }
```

- [ ] **Step 2: Verify it serves** — start the app against the UI harness (after Task 18) or `python -m pytest tests/ui/test_dashboard.py::test_desktop_renders_and_screenshot` once Task 19 updates selectors. For now: `python -c "from lywsd03mmc_monitor.web import create_app"` and open the page manually is optional. Commit the static skeleton.

- [ ] **Step 3: Commit**

```bash
git add lywsd03mmc_monitor/static/index.html
git commit -m "feat(ui): Aurora dashboard structure and styles"
```

---

## Task 15: Frontend — live polling, freshness, pulse, signal, Update

**Files:**
- Modify: `lywsd03mmc_monitor/static/index.html` (script section)

- [ ] **Step 1: Implement the live layer** — add JS:

```js
const $ = id => document.getElementById(id);
let last = {};               // previous values for pulse detection
function ago(ts){ if(!ts) return "—"; const s=Math.max(0,Math.floor(Date.now()/1000-ts));
  return s<60?`updated ${s}s ago`:s<3600?`updated ${Math.floor(s/60)}m ago`:`updated ${Math.floor(s/3600)}h ago`; }
function setVal(id, val, fmt){ const el=$(id); const t=val==null?"--":fmt(val);
  if(el.textContent!==t){ el.textContent=t; el.classList.remove("pulse"); void el.offsetWidth; el.classList.add("pulse"); } }
function renderBars(n){ const c=$("signalBars"); c.innerHTML="";
  for(let i=0;i<4;i++){ const b=document.createElement("i"); b.style.height=(5+i*4)+"px";
    b.style.background = i<n ? "#4cc9f0" : "#2b3a4a"; c.appendChild(b);} }

async function refreshCurrent(){
  let r; try{ r=await (await fetch("/api/current")).json(); }catch(e){ return; }
  setVal("temp", r.temperature, v=>v.toFixed(1));
  setVal("hum",  r.humidity,    v=>v.toFixed(0));
  setVal("bat",  r.battery,     v=>v);
  setVal("dew",  r.dew_point,   v=>v);
  setVal("abs",  r.absolute_humidity, v=>v);
  setVal("todayHigh", r.today_high, v=>"↑"+v.toFixed(1));
  setVal("todayLow",  r.today_low,  v=>"↓"+v.toFixed(1));
  $("comfort").textContent = r.comfort ? r.comfort[0].toUpperCase()+r.comfort.slice(1) : "—";
  $("tempUpdated").textContent = ago(r.temperature_ts);
  $("humUpdated").textContent  = ago(r.humidity_ts);
  $("batUpdated").textContent  = ago(r.battery_ts);
  $("batSub").textContent = (r.battery_source==="gatt" ? "via update" : "passive");
  $("liveBadge").textContent = r.online ? "Live" : "Offline";
  $("liveDot").style.background = r.online ? "#52d273" : "#5f7088";
  $("batbadge").innerHTML = r.battery_low ? '<span class="badge">Low battery</span>' : "";
  renderBars(r.signal_bars ?? 0);
}

async function doUpdate(){
  const b=$("updateBtn"); const prev=b.textContent; b.disabled=true;
  b.textContent="Connecting…";
  try{
    const resp=await fetch("/api/update",{method:"POST"});
    if(resp.status===409){ b.textContent="Sensor busy"; }
    else if(!resp.ok){ b.textContent="Failed"; }
    else { b.textContent="✓ Updated"; await refreshCurrent(); }
  }catch(e){ b.textContent="Failed"; }
  setTimeout(()=>{ b.textContent=prev||"↻ Update"; b.disabled=false; }, 1500);
}
$("updateBtn").addEventListener("click", doUpdate);
setInterval(refreshCurrent, 2000);
refreshCurrent();
```

Note: `ago()` recomputes every poll; also call a 1 s ticker that only refreshes the three `*Updated` labels so freshness counts up between polls (store last `r` globally and re-render labels).

- [ ] **Step 2: Verify** — covered by UI tests in Task 19 (signal bars render; Update button cycles; freshness present). Run after Task 19.

- [ ] **Step 3: Commit**

```bash
git add lywsd03mmc_monitor/static/index.html
git commit -m "feat(ui): live polling with per-field freshness, pulse, signal bars, Update action"
```

---

## Task 16: Frontend — synced pan/zoom charts with dynamic loading

**Files:**
- Modify: `lywsd03mmc_monitor/static/index.html` (script section)

- [ ] **Step 1: Implement charts** — register the zoom plugin and build two charts that share a window:

```js
let tempChart, humChart, win=null, extent=null, fetchTimer=null;
const COLORS={temp:"#9be4ff", hum:"#e879c9"};
function gradient(ctx,color){ const g=ctx.createLinearGradient(0,0,0,260);
  g.addColorStop(0,color+"44"); g.addColorStop(1,color+"00"); return g; }

function mkChart(canvas,color){
  const ctx=canvas.getContext("2d");
  return new Chart(ctx,{type:"line",
    data:{datasets:[{data:[],borderColor:color,backgroundColor:gradient(ctx,color),
      borderWidth:2,fill:true,tension:.35,pointRadius:0,spanGaps:false}]},
    options:{responsive:true,maintainAspectRatio:false,animation:false,
      plugins:{legend:{display:false},
        zoom:{ pan:{enabled:true,mode:"x",onPanComplete:onRangeChange},
               zoom:{wheel:{enabled:true},pinch:{enabled:true},mode:"x",onZoomComplete:onRangeChange},
               limits:{x:{}} }},
      scales:{x:{type:"time",grid:{color:"#ffffff10"},ticks:{color:"#7f90a8",maxTicksLimit:8}},
              y:{grid:{color:"#ffffff10"},ticks:{color:"#7f90a8"}}}}});
}

function applyWindowToCharts(){
  for(const c of [tempChart,humChart]){ c.options.scales.x.min=win.from*1000; c.options.scales.x.max=win.to*1000; }
}
function onRangeChange({chart}){
  // sync the OTHER chart to this one's x-range, then fetch
  const x=chart.options.scales.x; win={from:Math.round(x.min/1000), to:Math.round(x.max/1000)};
  const other = chart===tempChart?humChart:tempChart;
  other.options.scales.x.min=x.min; other.options.scales.x.max=x.max; other.update("none");
  clearTimeout(fetchTimer); fetchTimer=setTimeout(loadHistory,250);
}
function setLimits(){ const lim={min:extent.earliest*1000,max:extent.latest*1000};
  for(const c of [tempChart,humChart]){ c.options.plugins.zoom.limits.x=lim; } }

async function loadHistory(){
  if(!win) return;
  const r=await (await fetch(`/api/history?from=${win.from}&to=${win.to}`)).json();
  extent=r.extent;
  const tPts=[], hPts=[];
  if(r.points.length){ for(const p of r.points){ tPts.push({x:p.ts*1000,y:p.temperature}); hPts.push({x:p.ts*1000,y:p.humidity}); } }
  else { for(const b of r.bands){ tPts.push({x:b.hour_ts*1000,y:(b.min_temp+b.max_temp)/2}); hPts.push({x:b.hour_ts*1000,y:(b.min_hum+b.max_hum)/2}); }
         tPts.sort((a,b)=>a.x-b.x); hPts.sort((a,b)=>a.x-b.x); }
  tempChart.data.datasets[0].data=tPts; humChart.data.datasets[0].data=hPts;
  applyWindowToCharts(); if(extent&&extent.earliest) setLimits();
  tempChart.update("none"); humChart.update("none");
}

const RANGES={ "6h":21600,"24h":86400,"7d":604800 };
function setRange(r){ const now=Math.floor(Date.now()/1000);
  if(r==="all"){ if(extent&&extent.earliest){ win={from:extent.earliest,to:extent.latest}; } else { win={from:now-86400,to:now}; } }
  else { win={from:now-RANGES[r],to:now}; }
  document.querySelectorAll('#ranges button').forEach(b=>b.classList.toggle("active",b.dataset.r===r));
  loadHistory();
}
$("ranges").addEventListener("click",e=>{ if(e.target.tagName==="BUTTON") setRange(e.target.dataset.r); });

window.addEventListener("load",()=>{
  Chart.register(window.ChartZoom || window['chartjs-plugin-zoom']);
  tempChart=mkChart($("tempChart"),COLORS.temp);
  humChart =mkChart($("humChart"), COLORS.hum);
  setRange("24h");
});
```

After a successful backfill (Task 17), call `loadHistory()` again — the `/api/history` response carries the new `extent`, which updates the pan limits and lets the user scroll back into the just-filled past.

- [ ] **Step 2: Verify** — Task 19 UI test pans the temp chart and asserts the humidity chart's x-min matches (synced). Run after Task 19.

- [ ] **Step 3: Commit**

```bash
git add lywsd03mmc_monitor/static/index.html
git commit -m "feat(ui): synced pan/zoom charts with dynamic window loading and extent limits"
```

---

## Task 17: Frontend — labeled gap banner + backfill progress

**Files:**
- Modify: `lywsd03mmc_monitor/static/index.html` (script section)

- [ ] **Step 1: Implement gaps + backfill polling**:

```js
let gapState=null;
async function refreshGaps(){
  try{
    const now=Math.floor(Date.now()/1000);
    const r=await (await fetch(`/api/gaps?from=${now-604800}&to=${now}`)).json();
    gapState=r;
    const f=r.fillable||[], u=r.unrecoverable||[];
    if(!f.length && !u.length){ $("gapWrap").style.display="none"; return; }
    $("gapWrap").style.display="block";
    $("gapFillable").textContent = f.length ? `${f.length} hour(s) fillable` : "";
    $("fillBtn").style.display = f.length ? "" : "none";
    $("gapUnrecoverable").textContent = u.length
      ? `+ ${u.length} earlier hour(s) unavailable — beyond the sensor's ~2-day memory` : "";
  }catch(e){}
}

async function doFill(){
  const f=gapState && gapState.fillable; if(!f || !f.length) return;
  $("fillBtn").disabled=true;
  $("fillProgressWrap").style.display="block";
  $("fillProgressText").textContent="Connecting…"; $("fillProgressBar").style.width="0%";
  try{
    const resp=await fetch("/api/backfill",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({from:f[0], to:f[f.length-1]+3600})});
    if(resp.status===409){ $("fillProgressText").textContent="Sensor busy — try again"; }
    else if(!resp.ok){ $("fillProgressText").textContent="Backfill failed"; }
    else { await pollBackfill(); }
  }catch(e){ $("fillProgressText").textContent="Backfill failed"; }
  $("fillBtn").disabled=false;
  setTimeout(()=>{ $("fillProgressWrap").style.display="none"; }, 1500);
}

function pollBackfill(){
  return new Promise(resolve=>{
    const iv=setInterval(async()=>{
      let st; try{ st=await (await fetch("/api/backfill/status")).json(); }catch(e){ return; }
      if(st.state==="reading" && st.total){
        $("fillProgressText").textContent=`Reading history… ${st.received} / ${st.total} records`;
        $("fillProgressBar").style.width=Math.round(st.received/st.total*100)+"%";
      } else if(st.state==="connecting"){ $("fillProgressText").textContent="Connecting…"; }
      else if(st.state==="done"){ clearInterval(iv);
        $("fillProgressText").textContent=`✓ Filled ${st.filled} hour(s)`; $("fillProgressBar").style.width="100%";
        await refreshGaps(); await loadHistory();   // loadHistory picks up new extent -> can scroll into past
        resolve();
      } else if(st.state==="error"){ clearInterval(iv);
        $("fillProgressText").textContent="Backfill failed — try again"; resolve(); }
    }, 400);
  });
}
$("fillBtn").addEventListener("click", doFill);
refreshGaps(); setInterval(refreshGaps, 60000);
```

- [ ] **Step 2: Verify** — Task 19 UI test (with_gaps scenario) clicks Fill, waits for "Filled", asserts the fillable banner clears while the unrecoverable note (if any) stays. Run after Task 19.

- [ ] **Step 3: Commit**

```bash
git add lywsd03mmc_monitor/static/index.html
git commit -m "feat(ui): labeled gap banner and backfill progress flow"
```

---

## Task 18: UI harness — new signatures & scenarios

**Files:**
- Modify: `tests/ui/harness.py`

- [ ] **Step 1: Update `build_app`** — change `fake_backfill` to the new 3-arg progress signature, add a `fake_update`, and pass `update_fn`. Replace the relevant block:

```python
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
```

Also seed a boot epoch so the gap split is meaningful: after building the store in `with_gaps`, add `store.set_device_boot_epoch(NOW - 3 * 86400)` so gaps within 3 days are fillable.

- [ ] **Step 2: Run smoke** — `python -m pytest tests/ui/test_harness_smoke.py -q` → PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/ui/harness.py
git commit -m "test(ui): harness supports update_fn and progress backfill"
```

---

## Task 19: UI tests — selectors + new behaviors

**Files:**
- Modify: `tests/ui/test_dashboard.py`

- [ ] **Step 1: Update existing selectors** — replace the range-toggle test (chips are now `6h/24h/7d/all`):

```python
def test_range_toggle_moves_active_class(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        for r in ["6h", "24h", "7d", "all"]:
            page.click(f'button[data-r="{r}"]')
            assert "active" in page.get_attribute(f'button[data-r="{r}"]', "class")
        page.close()
```

`test_desktop_renders_and_screenshot` and the mobile test still use `#temp` — keep them.

- [ ] **Step 2: Add new behavior tests**:

```python
def test_signal_bars_render(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function("document.querySelectorAll('#signalBars i').length === 4")
        page.close()


def test_update_button_cycles(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.click("#updateBtn")
        page.wait_for_function("document.querySelector('#temp').textContent.includes('22.9')",
                               timeout=5000)
        page.close()


def test_backfill_clears_fillable_keeps_unrecoverable(pw):
    p, browser = pw
    with LiveServer(build_app("with_gaps")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#fillBtn")
        page.click("#fillBtn")
        page.wait_for_function(
            "document.querySelector('#fillProgressText').textContent.includes('Filled')",
            timeout=8000)
        # fillable banner clears
        page.wait_for_function(
            "document.querySelector('#gapFillable').textContent.trim() === ''",
            timeout=5000)
        page.close()


def test_charts_pan_in_sync(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function("window.tempChart && window.humChart && window.tempChart.data.datasets[0].data.length>0")
        synced = page.evaluate("""() => {
            tempChart.options.scales.x.min = 1000000; tempChart.options.scales.x.max = 2000000;
            tempChart.options.plugins.zoom.zoom.onZoomComplete({chart: tempChart});
            return humChart.options.scales.x.min === 1000000 && humChart.options.scales.x.max === 2000000;
        }""")
        assert synced
        page.close()
```

(For `test_charts_pan_in_sync` to work, assign `window.tempChart`/`window.humChart` in Task 16's `load` handler.)

- [ ] **Step 3: Run the full UI suite** — `python -m pytest tests/ui -q` → PASS. If `test_charts_pan_in_sync` needs the globals, add `window.tempChart=tempChart; window.humChart=humChart;` in the load handler and re-commit Task 16's file.

- [ ] **Step 4: Commit**

```bash
git add tests/ui/test_dashboard.py lywsd03mmc_monitor/static/index.html
git commit -m "test(ui): signal bars, Update cycle, backfill labeled clear, synced pan"
```

---

## Task 20: Full suite + hardware validation checklist

**Files:** none (verification)

- [ ] **Step 1: Run everything** — `python -m pytest -q` from repo root → all PASS. Capture the summary.

- [ ] **Step 2: Manual hardware validation** (real LYWSD03MMC, requires the physical sensor + BLE):
  1. `python -m lywsd03mmc_monitor` and open the dashboard.
  2. Confirm live values populate from passive adverts (staggered) and freshness counts up.
  3. Click **Update** → values refresh within ~2–4 s; battery shows "via update".
  4. Create/observe a gap; click **Fill from sensor** → progress bar advances with N/total; banner's fillable count clears; unrecoverable note (if any) remains.
  5. Pan/zoom the temperature chart → humidity chart stays in sync; after a fill, scroll back into newly filled history.
  6. Confirm the 4-bar signal indicator tracks distance from the sensor.

- [ ] **Step 3: Finalize** — see finishing-a-development-branch for merge/PR options.

---

## Self-Review

**Spec coverage:** redesign (Tasks 13–17), live/passive + freshness + pulse (15), 4-bar signal (2,15), interactive synced pan/zoom + dynamic load + extent (4,7,16), backfill progress (6,10,17), gap split fix (3,8,17), Update action (5,9,15), battery from both sources (2,5,9). All spec sections map to tasks.

**Placeholder scan:** no TBD/TODO; every code step has concrete code; the only environment-dependent step (Task 13 fetch) names an offline fallback.

**Type consistency:** `update(reading, rssi, now, source=)`, `read_live(...)->dict` with keys consumed in Task 9, `backfill(...,progress_cb)` consumed in Tasks 10/12/18, `classify_gaps->{fillable,unrecoverable}` consumed in Tasks 8/17, `/api/history` extent consumed in Task 16 — all consistent across producer/consumer task blocks.
