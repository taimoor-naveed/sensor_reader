# LYWSD03MMC Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-platform full-stack app that passively monitors a single Xiaomi LYWSD03MMC sensor over BLE, logs readings, serves a modern web dashboard, and can connect on-demand to backfill history gaps.

**Architecture:** One Python process running an asyncio event loop that hosts both a `bleak` BLE scanner and a FastAPI web server. A pure-function `protocol` module decodes/decrypts advertisements; a `store` module persists minute-resolution readings and hourly backfill in SQLite; a `state` object holds the live snapshot; a static HTML/JS dashboard polls a JSON API.

**Tech Stack:** Python 3.11+ (3.14 on dev machine), `bleak`, `cryptography` (AESCCM), `fastapi`, `uvicorn`, stdlib `sqlite3` + `tomllib`, Chart.js (vendored locally), `pytest` + `httpx` for tests.

## Global Constraints

- **Cross-platform:** must run on macOS (CoreBluetooth), Windows (WinRT), Linux (BlueZ). Only use `bleak` for BLE. Never assume a BLE MAC is available from the OS — on macOS it is a CoreBluetooth UUID. Obtain the sensor MAC for decryption from the advertisement payload itself, and obtain the connectable handle by capturing the `BLEDevice` during scanning.
- **Single sensor:** product ID `0x055B` only. No multi-sensor abstractions.
- **Secret handling:** the bindkey lives in `config.toml`, which is gitignored. Only `config.example.toml` is committed.
- **Package layout:** root-level package `lywsd03mmc_monitor/`; tests in `tests/`; run `pytest` from repo root. An empty `conftest.py` at repo root puts the root on `sys.path`.
- **Pure core:** `protocol.py`, `store.py`, `state.py`, `config.py` must contain no `bleak`/`fastapi` imports and be fully unit-testable without hardware.
- **Native, not Docker:** runs as a native process on macOS (dev) and Windows (prod) — BLE cannot be passed into a Docker container on either OS. No Docker.
- **One setup:** single `requirements.txt` and a single `python -m lywsd03mmc_monitor` entrypoint for dev and prod. No dev/prod split.
- **Default port 8787**, configurable in `config.toml` (avoids an existing web server on the Windows box).
- **Responsive + touch UI:** the dashboard must work on desktop and mobile, with touch-sized targets (≥44 px) and `tap` interactions; no hover-only controls.
- **Automated UI testing only:** all UI behavior is verified with Playwright (real Chromium) at desktop and mobile viewports against mock-seeded data — full-page screenshots for visual review plus interaction/edge-case tests. No manual UI testing.
- Bindkey is 16 bytes (32 hex chars). Temperature passive resolution is 0.1 °C; humidity 1%.

---

## Phase 1 — Passive monitoring (a complete working app on its own)

### Task 1: Project scaffolding & config loader

**Files:**
- Create: `requirements.txt`, `.gitignore`, `conftest.py`, `config.example.toml`, `lywsd03mmc_monitor/__init__.py`, `lywsd03mmc_monitor/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.load_config(path: str) -> Config`. `Config` is a dataclass with fields: `bindkey: bytes` (16 bytes), `address: str | None`, `host: str`, `port: int`, `offline_after_seconds: float`, `battery_warn_below: int`, `db_path: str`.

- [ ] **Step 1: Create `requirements.txt`**

```
bleak>=0.22
cryptography>=42
fastapi>=0.110
uvicorn>=0.29
pytest>=8
httpx>=0.27
playwright>=1.44
```

- [ ] **Step 2: Create `.gitignore`**

```
config.toml
*.db
*.db-wal
*.db-shm
__pycache__/
*.pyc
.venv/
venv/
```

- [ ] **Step 3: Create empty `conftest.py` and `lywsd03mmc_monitor/__init__.py`** (both empty files).

- [ ] **Step 4: Create `config.example.toml`**

```toml
[sensor]
# On macOS, `address` is the CoreBluetooth UUID, not a MAC. It is OPTIONAL:
# with a single sensor the scanner matches by service UUID + product id + decrypt success.
address = ""
bindkey = "00112233445566778899aabbccddeeff"  # 32 hex chars

[app]
host = "127.0.0.1"
port = 8787
offline_after_seconds = 150
battery_warn_below = 15
db_path = "sensor.db"
```

- [ ] **Step 5: Write the failing test** in `tests/test_config.py`

```python
from pathlib import Path
from lywsd03mmc_monitor.config import load_config


def test_load_config_parses_bindkey_to_bytes(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[sensor]\n'
        'address = ""\n'
        'bindkey = "00112233445566778899aabbccddeeff"\n'
        '[app]\n'
        'host = "127.0.0.1"\n'
        'port = 8000\n'
        'offline_after_seconds = 150\n'
        'battery_warn_below = 15\n'
        'db_path = "sensor.db"\n'
    )
    cfg = load_config(str(cfg_file))
    assert cfg.bindkey == bytes.fromhex("00112233445566778899aabbccddeeff")
    assert len(cfg.bindkey) == 16
    assert cfg.address is None
    assert cfg.port == 8000
    assert cfg.battery_warn_below == 15


def test_load_config_rejects_bad_bindkey(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[sensor]\nbindkey = "1234"\n[app]\n'
        'host="127.0.0.1"\nport=8000\noffline_after_seconds=150\n'
        'battery_warn_below=15\ndb_path="sensor.db"\n'
    )
    import pytest
    with pytest.raises(ValueError):
        load_config(str(cfg_file))
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` (config.py not written yet).

- [ ] **Step 7: Write `lywsd03mmc_monitor/config.py`**

```python
import tomllib
from dataclasses import dataclass


@dataclass
class Config:
    bindkey: bytes
    address: str | None
    host: str
    port: int
    offline_after_seconds: float
    battery_warn_below: int
    db_path: str


def load_config(path: str) -> Config:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    sensor = data.get("sensor", {})
    app = data.get("app", {})
    bindkey = bytes.fromhex(sensor.get("bindkey", ""))
    if len(bindkey) != 16:
        raise ValueError("bindkey must be 32 hex chars (16 bytes)")
    address = sensor.get("address") or None
    return Config(
        bindkey=bindkey,
        address=address,
        host=app.get("host", "127.0.0.1"),
        port=int(app.get("port", 8787)),
        offline_after_seconds=float(app.get("offline_after_seconds", 150)),
        battery_warn_below=int(app.get("battery_warn_below", 15)),
        db_path=app.get("db_path", "sensor.db"),
    )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 9: Commit**

```bash
git add requirements.txt .gitignore conftest.py config.example.toml lywsd03mmc_monitor tests/test_config.py
git commit -m "feat: project scaffolding and config loader"
```

---

### Task 2: Advertisement parsing — plaintext MiBeacon

**Files:**
- Create: `lywsd03mmc_monitor/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `Reading` dataclass: `temperature: float | None`, `humidity: float | None`, `battery: int | None` (all default `None`).
  - `parse_advertisement(service_data: bytes, bindkey: bytes) -> Reading | None` — returns `None` if not our product / malformed / decrypt fails.
  - `SERVICE_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"`, `PRODUCT_ID = 0x055B`.

- [ ] **Step 1: Write the failing test** (plaintext vector → 27.2 °C / 49 %)

```python
from lywsd03mmc_monitor.protocol import parse_advertisement, Reading


def test_parse_plaintext_temp_humidity():
    # frctrl=0x3050 (v3, mac+obj, not encrypted), product 0x055B, obj 0x100D
    data = bytes.fromhex("50305b05034c94b438c1a40d10041001ea01")
    r = parse_advertisement(data, b"\x00" * 16)
    assert r is not None
    assert round(r.temperature, 1) == 27.2
    assert round(r.humidity, 1) == 49.0


def test_parse_rejects_wrong_product():
    # product 0x16e4 instead of 0x055B
    data = bytes.fromhex("5030e41603")
    assert parse_advertisement(data, b"\x00" * 16) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocol.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `lywsd03mmc_monitor/protocol.py`** (plaintext path; decryption added next task)

```python
import struct
from dataclasses import dataclass

SERVICE_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
PRODUCT_ID = 0x055B


@dataclass
class Reading:
    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None


def parse_advertisement(service_data: bytes, bindkey: bytes) -> Reading | None:
    if len(service_data) < 5:
        return None
    frctrl = service_data[0] | (service_data[1] << 8)
    product_id = service_data[2] | (service_data[3] << 8)
    if product_id != PRODUCT_ID:
        return None
    mesh = (frctrl >> 7) & 1
    obj_include = (frctrl >> 6) & 1
    cap_include = (frctrl >> 5) & 1
    mac_include = (frctrl >> 4) & 1
    is_encrypted = (frctrl >> 3) & 1
    if mesh or not obj_include:
        return None

    i = 5
    mac = None
    if mac_include:
        mac = service_data[5:11]  # on-wire little-endian
        i += 6
    if cap_include:
        if i >= len(service_data):
            return None
        cap = service_data[i]
        i += 1
        if cap & 0x20:
            i += 1

    if is_encrypted:
        if mac is None:
            return None
        payload = decrypt_payload(service_data, mac, bindkey, i)
        if payload is None:
            return None
    else:
        payload = service_data[i:]

    return _parse_payload(payload)


def decrypt_payload(data: bytes, mac: bytes, bindkey: bytes, payload_start: int) -> bytes | None:
    return None  # implemented in Task 3


def _parse_payload(payload: bytes) -> Reading:
    reading = Reading()
    p = 0
    while p + 3 <= len(payload):
        obj_type = payload[p] | (payload[p + 1] << 8)
        length = payload[p + 2]
        value = payload[p + 3:p + 3 + length]
        if len(value) < length:
            break
        _apply_object(reading, obj_type, value)
        p += 3 + length
    return reading


def _apply_object(reading: Reading, obj_type: int, value: bytes) -> None:
    if obj_type == 0x100D and len(value) == 4:
        temp, humi = struct.unpack("<hH", value)
        reading.temperature = temp / 10
        reading.humidity = humi / 10
    elif obj_type == 0x1004 and len(value) == 2:
        reading.temperature = struct.unpack("<h", value)[0] / 10
    elif obj_type == 0x1006 and len(value) == 2:
        reading.humidity = int(struct.unpack("<H", value)[0] / 10)
    elif obj_type == 0x100A and len(value) == 1:
        reading.battery = value[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_protocol.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/protocol.py tests/test_protocol.py
git commit -m "feat: parse plaintext MiBeacon advertisements"
```

---

### Task 3: Advertisement decryption — AES-CCM v4/v5

**Files:**
- Modify: `lywsd03mmc_monitor/protocol.py` (replace `decrypt_payload` body)
- Test: `tests/test_protocol.py` (add)

**Interfaces:**
- Consumes: `decrypt_payload(data, mac, bindkey, payload_start)` signature from Task 2.
- Produces: working `decrypt_payload` returning the decrypted TLV payload bytes or `None` on auth failure.

Construction (verified against the canonical vector below): `nonce = mac(6) + data[2:5] + data[-7:-4]` (12 bytes), `ciphertext = data[payload_start:-7]`, `mic = data[-4:]`, `AAD = b"\x11"`, cipher = `AESCCM(bindkey, tag_length=4)`.

- [ ] **Step 1: Write the failing test** (canonical encrypted vector decrypts to obj `0x4C02` = 58 %)

```python
from lywsd03mmc_monitor.protocol import decrypt_payload


def test_decrypt_payload_known_vector():
    # service data bytes, bindkey, and expected plaintext from xiaomi-ble test vectors
    data = bytes.fromhex("5858e4162c84535638c1a42b6ef2e91200006c884d9e")
    bindkey = bytes.fromhex("a115210eed7a88e50ad52662e732a9fb")
    mac = data[5:11]            # 84 53 56 38 c1 a4
    payload_start = 11          # frctrl=0x5858 -> version5, mac included -> 5+6
    plaintext = decrypt_payload(data, mac, bindkey, payload_start)
    assert plaintext == bytes.fromhex("024c013a")  # obj 0x4C02, len 1, value 0x3a = 58


def test_decrypt_payload_wrong_key_returns_none():
    data = bytes.fromhex("5858e4162c84535638c1a42b6ef2e91200006c884d9e")
    assert decrypt_payload(data, data[5:11], b"\x00" * 16, 11) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocol.py::test_decrypt_payload_known_vector -v`
Expected: FAIL (`decrypt_payload` returns `None`).

- [ ] **Step 3: Implement `decrypt_payload`** — replace the stub and add the import at top of `protocol.py`

```python
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
```

```python
def decrypt_payload(data: bytes, mac: bytes, bindkey: bytes, payload_start: int) -> bytes | None:
    if len(data) < payload_start + 7:
        return None
    nonce = bytes(mac) + data[2:5] + data[-7:-4]
    mic = data[-4:]
    ciphertext = data[payload_start:-7]
    try:
        return AESCCM(bindkey, tag_length=4).decrypt(nonce, ciphertext + mic, b"\x11")
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_protocol.py -v`
Expected: PASS (all protocol tests pass).

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/protocol.py tests/test_protocol.py
git commit -m "feat: AES-CCM decryption of encrypted advertisements"
```

---

### Task 4: Comfort math (dew point, absolute humidity, comfort band)

**Files:**
- Modify: `lywsd03mmc_monitor/protocol.py` (append functions)
- Test: `tests/test_protocol.py` (add)

**Interfaces:**
- Produces:
  - `dew_point(temp_c: float, rh: float) -> float`
  - `absolute_humidity(temp_c: float, rh: float) -> float` (g/m³)
  - `comfort_band(temp_c: float, rh: float) -> str` (one of `"comfortable"`, `"cold"`, `"hot"`, `"dry"`, `"humid"`)

- [ ] **Step 1: Write the failing test**

```python
from lywsd03mmc_monitor.protocol import dew_point, absolute_humidity, comfort_band


def test_dew_point_at_saturation_equals_temperature():
    assert abs(dew_point(20.0, 100.0) - 20.0) < 0.1


def test_dew_point_realistic():
    assert abs(dew_point(27.2, 49.0) - 15.5) < 0.3


def test_absolute_humidity_realistic():
    assert abs(absolute_humidity(27.2, 49.0) - 12.8) < 0.3


def test_comfort_band():
    assert comfort_band(22.0, 45.0) == "comfortable"
    assert comfort_band(10.0, 45.0) == "cold"
    assert comfort_band(30.0, 45.0) == "hot"
    assert comfort_band(22.0, 20.0) == "dry"
    assert comfort_band(22.0, 70.0) == "humid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocol.py -k comfort or dew or absolute -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the functions** — append to `protocol.py` and add `import math` at top

```python
def dew_point(temp_c: float, rh: float) -> float:
    a, b = 17.27, 237.7
    gamma = (a * temp_c) / (b + temp_c) + math.log(rh / 100.0)
    return (b * gamma) / (a - gamma)


def absolute_humidity(temp_c: float, rh: float) -> float:
    saturation = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    return saturation * rh * 2.1674 / (273.15 + temp_c)


def comfort_band(temp_c: float, rh: float) -> str:
    if temp_c < 18.0:
        return "cold"
    if temp_c > 27.0:
        return "hot"
    if rh < 30.0:
        return "dry"
    if rh > 60.0:
        return "humid"
    return "comfortable"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_protocol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/protocol.py tests/test_protocol.py
git commit -m "feat: comfort math (dew point, absolute humidity, band)"
```

---

### Task 5: SQLite store — schema, minute-deduped inserts, range query

**Files:**
- Create: `lywsd03mmc_monitor/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces `Store`:
  - `Store(path: str)` — opens/creates DB (WAL), creates tables.
  - `add_reading(ts: int, temperature: float | None, humidity: float | None, battery: int | None, rssi: int | None) -> None` — stores at minute-aligned ts, replacing any existing row for that minute.
  - `readings_between(start: int, end: int) -> list[dict]` — rows with keys `ts, temperature, humidity, battery, rssi`, ascending.
  - `history` table exists for later tasks.

- [ ] **Step 1: Write the failing test**

```python
from lywsd03mmc_monitor.store import Store


def test_add_reading_dedupes_to_minute(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(1000, 20.0, 40.0, 90, -60)   # minute floor 960
    s.add_reading(1030, 21.0, 41.0, 89, -61)   # same minute -> replaces
    rows = s.readings_between(0, 2000)
    assert len(rows) == 1
    assert rows[0]["ts"] == 960
    assert rows[0]["temperature"] == 21.0
    assert rows[0]["battery"] == 89


def test_readings_between_orders_and_filters(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(60, 1.0, 10.0, 50, -50)
    s.add_reading(180, 2.0, 11.0, 50, -50)
    s.add_reading(360, 3.0, 12.0, 50, -50)
    rows = s.readings_between(120, 300)
    assert [r["ts"] for r in rows] == [180]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `lywsd03mmc_monitor/store.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/store.py tests/test_store.py
git commit -m "feat: SQLite store with minute-deduped readings"
```

---

### Task 6: Gap detection

**Files:**
- Modify: `lywsd03mmc_monitor/store.py` (add method)
- Test: `tests/test_store.py` (add)

**Interfaces:**
- Produces: `Store.fillable_gaps(start: int, end: int) -> list[int]` — list of top-of-hour unix timestamps within `[start, end)` that have **no** readings **and** no existing history row. Ascending.

- [ ] **Step 1: Write the failing test**

```python
def test_fillable_gaps_finds_empty_hours(tmp_path):
    from lywsd03mmc_monitor.store import Store
    s = Store(str(tmp_path / "t.db"))
    h0, h1, h2 = 0, 3600, 7200
    s.add_reading(h0 + 60, 20.0, 40.0, 90, -60)   # hour 0 has data
    s.add_reading(h2 + 60, 22.0, 42.0, 90, -60)   # hour 2 has data
    # hour 1 (3600) is empty -> a gap
    assert s.fillable_gaps(0, 10800) == [3600]


def test_fillable_gaps_skips_hours_with_history(tmp_path):
    from lywsd03mmc_monitor.store import Store
    s = Store(str(tmp_path / "t.db"))
    s.conn.execute(
        "INSERT INTO history VALUES (3600, 18.0, 19.0, 40, 45)")
    s.conn.commit()
    assert s.fillable_gaps(0, 7200) == [0]  # hour 1 already backfilled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -k gaps -v`
Expected: FAIL (`AttributeError: fillable_gaps`).

- [ ] **Step 3: Add the method to `Store`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/store.py tests/test_store.py
git commit -m "feat: detect fillable whole-hour gaps"
```

---

### Task 7: Live state (snapshot, online/offline, battery warning)

**Files:**
- Create: `lywsd03mmc_monitor/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `protocol.Reading`, `protocol.dew_point/absolute_humidity/comfort_band`.
- Produces `LiveState`:
  - `LiveState(offline_after: float, battery_warn_below: int)`.
  - `update(reading: Reading, rssi: int, now: float) -> None` — merges non-None fields, sets `rssi` and `last_seen=now`.
  - `is_online(now: float) -> bool`, `battery_low() -> bool`.
  - `snapshot(now: float) -> dict` with keys: `temperature, humidity, battery, rssi, last_seen, online, battery_low, dew_point, absolute_humidity, comfort` (derived keys `None` until both temp+humidity known).

- [ ] **Step 1: Write the failing test**

```python
from lywsd03mmc_monitor.protocol import Reading
from lywsd03mmc_monitor.state import LiveState


def test_update_merges_and_tracks_last_seen():
    st = LiveState(offline_after=150, battery_warn_below=15)
    st.update(Reading(temperature=27.2, humidity=49.0, battery=80), rssi=-55, now=1000.0)
    snap = st.snapshot(now=1000.0)
    assert snap["temperature"] == 27.2
    assert snap["online"] is True
    assert snap["battery_low"] is False
    assert abs(snap["dew_point"] - 15.5) < 0.3
    assert snap["comfort"] == "hot"


def test_partial_update_keeps_previous():
    st = LiveState(offline_after=150, battery_warn_below=15)
    st.update(Reading(temperature=20.0, humidity=40.0, battery=80), -55, 1000.0)
    st.update(Reading(humidity=42.0), -56, 1010.0)  # humidity-only object
    snap = st.snapshot(1010.0)
    assert snap["temperature"] == 20.0
    assert snap["humidity"] == 42.0


def test_offline_and_low_battery():
    st = LiveState(offline_after=150, battery_warn_below=15)
    st.update(Reading(temperature=20.0, humidity=40.0, battery=10), -55, 1000.0)
    assert st.is_online(1000.0 + 100) is True
    assert st.is_online(1000.0 + 200) is False
    assert st.battery_low() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `lywsd03mmc_monitor/state.py`**

```python
from lywsd03mmc_monitor import protocol


class LiveState:
    def __init__(self, offline_after: float, battery_warn_below: int):
        self.offline_after = offline_after
        self.battery_warn_below = battery_warn_below
        self.temperature = None
        self.humidity = None
        self.battery = None
        self.rssi = None
        self.last_seen = None

    def update(self, reading, rssi, now) -> None:
        if reading.temperature is not None:
            self.temperature = reading.temperature
        if reading.humidity is not None:
            self.humidity = reading.humidity
        if reading.battery is not None:
            self.battery = reading.battery
        self.rssi = rssi
        self.last_seen = now

    def is_online(self, now) -> bool:
        return self.last_seen is not None and (now - self.last_seen) <= self.offline_after

    def battery_low(self) -> bool:
        return self.battery is not None and self.battery < self.battery_warn_below

    def snapshot(self, now) -> dict:
        dew = ah = comfort = None
        if self.temperature is not None and self.humidity is not None:
            dew = round(protocol.dew_point(self.temperature, self.humidity), 1)
            ah = round(protocol.absolute_humidity(self.temperature, self.humidity), 1)
            comfort = protocol.comfort_band(self.temperature, self.humidity)
        return {
            "temperature": self.temperature,
            "humidity": self.humidity,
            "battery": self.battery,
            "rssi": self.rssi,
            "last_seen": self.last_seen,
            "online": self.is_online(now),
            "battery_low": self.battery_low(),
            "dew_point": dew,
            "absolute_humidity": ah,
            "comfort": comfort,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/state.py tests/test_state.py
git commit -m "feat: live state snapshot with online/battery status"
```

---

### Task 8: BLE scanner (passive)

**Files:**
- Create: `lywsd03mmc_monitor/scanner.py`
- Test: `tests/test_scanner.py`

**Interfaces:**
- Consumes: `protocol.parse_advertisement`, `protocol.SERVICE_UUID`, `LiveState`, `Store`.
- Produces `Scanner`:
  - `Scanner(bindkey, store, state, now_fn, address=None)`.
  - `handle_detection(device, advertisement_data) -> None` — pure-ish: extracts service data for `SERVICE_UUID`, parses, updates state + store, and stores the connectable `device` on `self.last_device`. **This method is unit-testable with fakes.**
  - `async start()` / `async stop()` — wrap `BleakScanner` using `handle_detection` as the callback. (Not unit-tested; exercised manually.)

This task tests `handle_detection` with fake objects so no hardware is needed. The thin `bleak` wrapper around it is verified manually in Task 10.

- [ ] **Step 1: Write the failing test**

```python
from lywsd03mmc_monitor.protocol import SERVICE_UUID
from lywsd03mmc_monitor.state import LiveState
from lywsd03mmc_monitor.store import Store
from lywsd03mmc_monitor.scanner import Scanner


class FakeAdv:
    def __init__(self, service_data, rssi):
        self.service_data = service_data
        self.rssi = rssi


class FakeDevice:
    def __init__(self, address):
        self.address = address


def test_handle_detection_updates_state_and_store(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(offline_after=150, battery_warn_below=15)
    clock = [1000.0]
    sc = Scanner(bindkey=b"\x00" * 16, store=store, state=state,
                 now_fn=lambda: clock[0])
    data = bytes.fromhex("50305b05034c94b438c1a40d10041001ea01")  # 27.2C/49%
    dev = FakeDevice("AA:BB")
    sc.handle_detection(dev, FakeAdv({SERVICE_UUID: data}, rssi=-50))

    snap = state.snapshot(1000.0)
    assert round(snap["temperature"], 1) == 27.2
    assert snap["rssi"] == -50
    assert sc.last_device is dev
    assert len(store.readings_between(0, 2000)) == 1


def test_handle_detection_ignores_other_service_data(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(150, 15)
    sc = Scanner(b"\x00" * 16, store, state, now_fn=lambda: 1.0)
    sc.handle_detection(object(), FakeAdv({"0000180f-0000-1000-8000-00805f9b34fb": b"\x00"}, -50))
    assert state.snapshot(1.0)["temperature"] is None
    assert sc.last_device is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scanner.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `lywsd03mmc_monitor/scanner.py`**

```python
from lywsd03mmc_monitor import protocol


class Scanner:
    def __init__(self, bindkey, store, state, now_fn, address=None):
        self.bindkey = bindkey
        self.store = store
        self.state = state
        self.now_fn = now_fn
        self.address = address
        self.last_device = None
        self._bleak_scanner = None

    def handle_detection(self, device, advertisement_data) -> None:
        service_data = getattr(advertisement_data, "service_data", {}) or {}
        raw = service_data.get(protocol.SERVICE_UUID)
        if raw is None:
            return
        reading = protocol.parse_advertisement(bytes(raw), self.bindkey)
        if reading is None:
            return
        now = self.now_fn()
        rssi = getattr(advertisement_data, "rssi", None)
        self.state.update(reading, rssi, now)
        self.store.add_reading(
            int(now), reading.temperature, reading.humidity,
            reading.battery, rssi,
        )
        self.last_device = device

    async def start(self) -> None:
        from bleak import BleakScanner
        self._bleak_scanner = BleakScanner(detection_callback=self.handle_detection)
        await self._bleak_scanner.start()

    async def stop(self) -> None:
        if self._bleak_scanner is not None:
            await self._bleak_scanner.stop()
            self._bleak_scanner = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scanner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/scanner.py tests/test_scanner.py
git commit -m "feat: passive BLE scanner with testable detection handler"
```

---

### Task 9: Web API (`/api/current`, `/api/history`)

**Files:**
- Create: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `Store`, `LiveState`.
- Produces:
  - `create_app(store, state, now_fn, backfill_fn=None) -> FastAPI`.
  - `GET /api/current` → `state.snapshot(now)` as JSON.
  - `GET /api/history?range=hour|day|week` → `{"range": str, "points": [...], "bands": [...]}`. For `hour`/`day`: `points` = `readings_between` over the window, `bands` = `history` rows in window. For `week`: `points` = `[]`, `bands` = hourly rollup of readings + history rows. (`/api/gaps` and `/api/backfill` added in Phase 2.)
  - `GET /` → serves `static/index.html`.
- Window math: `now` from `now_fn`; `hour`=3600 s, `day`=86400 s, `week`=604800 s back from now.

- [ ] **Step 1: Add the rollup helper to `Store` first** (needed by week view) — modify `store.py`, add:

```python
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
```

- [ ] **Step 2: Write the failing test** in `tests/test_web.py`

```python
from fastapi.testclient import TestClient
from lywsd03mmc_monitor.protocol import Reading
from lywsd03mmc_monitor.state import LiveState
from lywsd03mmc_monitor.store import Store
from lywsd03mmc_monitor.web import create_app


def _client(tmp_path, now):
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(offline_after=150, battery_warn_below=15)
    app = create_app(store, state, now_fn=lambda: now)
    return store, state, TestClient(app)


def test_current_endpoint(tmp_path):
    store, state, client = _client(tmp_path, now=1000.0)
    state.update(Reading(temperature=21.0, humidity=44.0, battery=88), -52, 1000.0)
    r = client.get("/api/current").json()
    assert r["temperature"] == 21.0
    assert r["online"] is True
    assert r["comfort"] == "comfortable"


def test_history_day_returns_points(tmp_path):
    now = 100000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 600, 20.0, 40.0, 90, -60)
    r = client.get("/api/history?range=day").json()
    assert r["range"] == "day"
    assert any(p["temperature"] == 20.0 for p in r["points"])


def test_history_week_returns_bands(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 7200, 18.0, 30.0, 90, -60)
    store.add_reading(now - 7100, 22.0, 36.0, 90, -60)  # same hour -> min/max band
    r = client.get("/api/history?range=week").json()
    assert r["points"] == []
    assert any(b["min_temp"] == 18.0 and b["max_temp"] == 22.0 for b in r["bands"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_web.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Write `lywsd03mmc_monitor/web.py`**

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

_STATIC = Path(__file__).parent / "static"
_RANGES = {"hour": 3600, "day": 86400, "week": 604800}


def create_app(store, state, now_fn, backfill_fn=None) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/current")
    def current():
        return JSONResponse(state.snapshot(now_fn()))

    @app.get("/api/history")
    def history(range: str = "day"):
        span = _RANGES.get(range, 86400)
        now = int(now_fn())
        start, end = now - span, now
        if range == "week":
            points = []
            bands = store.hourly_rollup(start, end) + store.history_between(start, end)
            bands.sort(key=lambda b: b["hour_ts"])
        else:
            points = store.readings_between(start, end)
            bands = store.history_between(start, end)
        return {"range": range, "points": points, "bands": bands}

    # Phase 2 endpoints registered here by Tasks 13-14 (gaps, backfill).
    app.state.store = store
    app.state.live = state
    app.state.now_fn = now_fn
    app.state.backfill_fn = backfill_fn
    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web.py -v`
Expected: PASS (3 passed). (`/` returns 404 in tests until Task 11 creates `index.html`; not exercised here.)

- [ ] **Step 6: Commit**

```bash
git add lywsd03mmc_monitor/web.py lywsd03mmc_monitor/store.py tests/test_web.py
git commit -m "feat: web API for current readings and history"
```

---

### Task 10: Application entrypoint (wire scanner + web in one event loop)

**Files:**
- Create: `lywsd03mmc_monitor/main.py`, `lywsd03mmc_monitor/__main__.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `load_config`, `Store`, `LiveState`, `Scanner`, `create_app`.
- Produces: `async def run(config_path: str) -> None` and a `python -m lywsd03mmc_monitor` entry.

No unit test (integration/hardware). Verified manually against the real sensor.

- [ ] **Step 1: Write `lywsd03mmc_monitor/main.py`**

```python
import asyncio
import time

import uvicorn

from lywsd03mmc_monitor.config import load_config
from lywsd03mmc_monitor.scanner import Scanner
from lywsd03mmc_monitor.state import LiveState
from lywsd03mmc_monitor.store import Store
from lywsd03mmc_monitor.web import create_app


async def run(config_path: str = "config.toml") -> None:
    cfg = load_config(config_path)
    store = Store(cfg.db_path)
    state = LiveState(cfg.offline_after_seconds, cfg.battery_warn_below)
    scanner = Scanner(cfg.bindkey, store, state, now_fn=time.time, address=cfg.address)

    def backfill_fn(start_ts: int, end_ts: int):
        from lywsd03mmc_monitor.gatt import backfill  # Phase 2
        return backfill(scanner.last_device, store, start_ts, end_ts)

    app = create_app(store, state, now_fn=time.time, backfill_fn=backfill_fn)

    await scanner.start()
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info"))
    try:
        await server.serve()
    finally:
        await scanner.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write `lywsd03mmc_monitor/__main__.py`**

```python
from lywsd03mmc_monitor.main import main

main()
```

- [ ] **Step 3: Write `README.md`** (setup + run + macOS permission note)

```markdown
# LYWSD03MMC Monitor

A small from-scratch app that monitors one Xiaomi LYWSD03MMC sensor over BLE and serves a dashboard.

## Setup
```
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml                   # then edit in your bindkey
```

## Run
```
python -m lywsd03mmc_monitor
```
Open http://127.0.0.1:8787  (default port; change it in config.toml)

## Notes
- **macOS:** the first run prompts for Bluetooth permission for your terminal/app; allow it.
  The sensor's OS-level address is a CoreBluetooth UUID, not a MAC — this is expected and handled.
- **Windows/Linux:** no extra steps; Bluetooth must be on.
```

- [ ] **Step 4: Live hardware check** (run by the developer/assistant on the dev Mac — not the user)

Run: `cp config.example.toml config.toml`, edit in the real bindkey, then `python -m lywsd03mmc_monitor`.
Expected: console shows uvicorn started on port 8787; within ~10 s `curl http://127.0.0.1:8787/api/current` returns live temperature/humidity that changes over successive calls. This is the only step that touches the real sensor; all UI behavior is covered by the automated Phase 3 tests.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/main.py lywsd03mmc_monitor/__main__.py README.md
git commit -m "feat: application entrypoint wiring scanner and web server"
```

---

### Task 11: Dashboard UI (modern, flowing charts)

**Files:**
- Create: `lywsd03mmc_monitor/static/index.html`
- Create: `lywsd03mmc_monitor/static/vendor/chart.umd.min.js` (vendored, downloaded once)

**Interfaces:**
- Consumes: `GET /api/current`, `GET /api/history?range=…`. (Gap UI added in Phase 2, Task 15.)

No automated test; visual verification in the browser.

- [ ] **Step 1: Vendor Chart.js locally** (one-time download; runtime stays offline)

Run:
```bash
mkdir -p lywsd03mmc_monitor/static/vendor
curl -L https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js \
  -o lywsd03mmc_monitor/static/vendor/chart.umd.min.js
```
Expected: file exists and is ~200 KB.

- [ ] **Step 2: Write `lywsd03mmc_monitor/static/index.html`** — a modern dark dashboard with hero readings, derived metrics, a battery badge, range toggle, and two gradient-filled flowing line charts.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sensor</title>
<script src="/static/vendor/chart.umd.min.js"></script>
<style>
  :root {
    --bg:#0f1419; --card:#1a2027; --card2:#222a33; --txt:#e8edf2;
    --muted:#8a97a6; --accent:#4cc9f0; --accent2:#f72585; --ok:#52d273; --warn:#ffb454;
    --radius:18px;
  }
  *{box-sizing:border-box}
  html,body{max-width:100%;overflow-x:hidden}
  body{margin:0;font-family:-apple-system,Segoe UI,Roboto,system-ui,sans-serif;
       background:radial-gradient(1200px 600px at 70% -10%,#16202b,var(--bg));
       color:var(--txt);min-height:100vh;padding:clamp(14px,4vw,28px)}
  h1{font-size:15px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;
     color:var(--muted);margin:0 0 20px}
  .grid{display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));max-width:1100px;margin:0 auto}
  .card{background:linear-gradient(160deg,var(--card),var(--card2));border:1px solid #ffffff10;
        border-radius:var(--radius);padding:22px;box-shadow:0 10px 30px #0006}
  .label{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
  .value{font-size:clamp(34px,9vw,46px);font-weight:700;margin-top:6px;line-height:1}
  .unit{font-size:18px;color:var(--muted);font-weight:500}
  .sub{margin-top:10px;font-size:13px;color:var(--muted)}
  .status{display:inline-flex;align-items:center;gap:7px;font-size:15px}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 10px var(--ok)}
  .dot.off{background:var(--muted);box-shadow:none}
  .badge{display:inline-block;margin-left:10px;padding:3px 10px;border-radius:999px;
         font-size:12px;background:#ffb45422;color:var(--warn);border:1px solid #ffb45455}
  .charts{max-width:1100px;margin:22px auto 0}
  .toolbar{display:flex;gap:8px;margin:0 0 12px;flex-wrap:wrap}
  .toolbar button{background:var(--card);color:var(--muted);border:1px solid #ffffff14;
     min-height:44px;padding:10px 18px;border-radius:999px;cursor:pointer;font-size:14px;
     transition:.2s;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
  .toolbar button.active{background:var(--accent);color:#04222e;border-color:transparent;font-weight:600}
  .chartcard{margin-bottom:18px}
  .chartbox{position:relative;height:clamp(200px,38vw,280px);margin-top:8px}
  canvas{width:100%!important}
  .pill{font-size:13px;min-height:40px;padding:8px 16px;border-radius:999px;background:#ffffff12;
        color:var(--muted);touch-action:manipulation;-webkit-tap-highlight-color:transparent}
  @media (max-width:600px){
    .grid{grid-template-columns:1fr 1fr;gap:12px}
    .card{padding:16px}
    h1{margin-bottom:14px}
  }
  @media (max-width:380px){ .grid{grid-template-columns:1fr} }
</style>
</head>
<body>
  <h1>LYWSD03MMC Monitor</h1>
  <div class="grid">
    <div class="card">
      <div class="label">Temperature</div>
      <div class="value"><span id="temp">--</span><span class="unit"> °C</span></div>
      <div class="sub" id="comfort">—</div>
    </div>
    <div class="card">
      <div class="label">Humidity</div>
      <div class="value"><span id="hum">--</span><span class="unit"> %</span></div>
      <div class="sub">Dew point <span id="dew">—</span> °C · Abs <span id="abs">—</span> g/m³</div>
    </div>
    <div class="card">
      <div class="label">Battery</div>
      <div class="value"><span id="bat">--</span><span class="unit"> %</span><span id="batbadge"></span></div>
      <div class="sub">Signal <span id="rssi">—</span> dBm</div>
    </div>
    <div class="card">
      <div class="label">Status</div>
      <div class="value" style="font-size:26px;margin-top:12px">
        <span class="status"><span class="dot" id="dot"></span><span id="online">—</span></span>
      </div>
      <div class="sub" id="seen">—</div>
    </div>
  </div>

  <div class="charts">
    <div class="toolbar" id="ranges">
      <button data-r="hour">Hour</button>
      <button data-r="day" class="active">Day</button>
      <button data-r="week">Week</button>
    </div>
    <div class="card chartcard"><div class="label">Temperature</div><div class="chartbox"><canvas id="tempChart"></canvas></div></div>
    <div class="card chartcard"><div class="label">Humidity</div><div class="chartbox"><canvas id="humChart"></canvas></div></div>
  </div>

<script>
const $ = id => document.getElementById(id);
let range = "day", tempChart, humChart;

function gradient(ctx, color){
  const g = ctx.createLinearGradient(0,0,0,260);
  g.addColorStop(0, color+"66"); g.addColorStop(1, color+"00"); return g;
}
function mkChart(canvas, color){
  const ctx = canvas.getContext("2d");
  return new Chart(ctx, {
    type:"line",
    data:{datasets:[{data:[], borderColor:color, backgroundColor:gradient(ctx,color),
      borderWidth:2, fill:true, tension:.4, pointRadius:0, spanGaps:false}]},
    options:{responsive:true, maintainAspectRatio:false,
      animation:{duration:600, easing:"easeOutQuart"},
      plugins:{legend:{display:false}},
      scales:{x:{type:"time", grid:{color:"#ffffff10"}, ticks:{color:"#8a97a6"}},
              y:{grid:{color:"#ffffff10"}, ticks:{color:"#8a97a6"}}}}
  });
}

async function refreshCurrent(){
  const r = await (await fetch("/api/current")).json();
  $("temp").textContent = r.temperature?.toFixed(1) ?? "--";
  $("hum").textContent  = r.humidity?.toFixed(0) ?? "--";
  $("bat").textContent  = r.battery ?? "--";
  $("rssi").textContent = r.rssi ?? "—";
  $("dew").textContent  = r.dew_point ?? "—";
  $("abs").textContent  = r.absolute_humidity ?? "—";
  $("comfort").textContent = r.comfort ? r.comfort[0].toUpperCase()+r.comfort.slice(1) : "—";
  $("online").textContent = r.online ? "Live" : "Offline";
  $("dot").className = "dot" + (r.online ? "" : " off");
  $("seen").textContent = r.last_seen ? "Last seen " + new Date(r.last_seen*1000).toLocaleTimeString() : "—";
  $("batbadge").innerHTML = r.battery_low ? '<span class="badge">Low battery</span>' : "";
}

async function refreshHistory(){
  const r = await (await fetch("/api/history?range="+range)).json();
  const tPts = r.points.map(p => ({x:p.ts*1000, y:p.temperature}));
  const hPts = r.points.map(p => ({x:p.ts*1000, y:p.humidity}));
  // For week view, draw band midpoints as the line so device + live bands render uniformly.
  if(r.points.length === 0 && r.bands.length){
    for(const b of r.bands){
      tPts.push({x:b.hour_ts*1000, y:(b.min_temp+b.max_temp)/2});
      hPts.push({x:b.hour_ts*1000, y:(b.min_hum+b.max_hum)/2});
    }
    tPts.sort((a,b)=>a.x-b.x); hPts.sort((a,b)=>a.x-b.x);
  }
  tempChart.data.datasets[0].data = tPts; tempChart.update();
  humChart.data.datasets[0].data  = hPts; humChart.update();
}

$("ranges").addEventListener("click", e => {
  if(e.target.tagName !== "BUTTON") return;
  range = e.target.dataset.r;
  document.querySelectorAll("#ranges button").forEach(b=>b.classList.toggle("active", b===e.target));
  refreshHistory();
});

window.addEventListener("load", () => {
  tempChart = mkChart($("tempChart"), "#4cc9f0");
  humChart  = mkChart($("humChart"),  "#b5179e");
  refreshCurrent(); refreshHistory();
  setInterval(refreshCurrent, 5000);
  setInterval(refreshHistory, 60000);
});
</script>
</body>
</html>
```

> Note: Chart.js needs a time scale adapter for `type:"time"`. To keep runtime fully offline and avoid an extra adapter file, if the time axis errors at runtime, change both `x` scales to `type:"linear"` and format ticks with a callback `v => new Date(v).toLocaleTimeString()`. Verify in the browser and pick whichever renders; commit the working version.

- [ ] **Step 3: Verification deferred to Phase 3**

This dashboard's appearance and behavior are verified by the automated Playwright suite in Phase 3 (Tasks 16–17): desktop + mobile screenshots and interaction tests against mock-seeded data. No manual verification here.

- [ ] **Step 4: Commit**

```bash
git add lywsd03mmc_monitor/static/
git commit -m "feat: modern dashboard with flowing charts"
```

---

**End of Phase 1: a complete, working passive-monitoring app with dashboard.**

---

## Phase 2 — On-demand gap backfill (active GATT)

### Task 12: Parse device history records & device clock

**Files:**
- Modify: `lywsd03mmc_monitor/protocol.py` (append)
- Test: `tests/test_protocol.py` (add)

**Interfaces:**
- Produces:
  - `HistoryRecord` dataclass: `index:int, ts_offset:int, min_temp:float, max_temp:float, min_hum:int, max_hum:int`.
  - `parse_history_record(data: bytes) -> HistoryRecord` — layout `<IIhBhB`: index, ts_offset(seconds since device start), max_temp/10, max_hum, min_temp/10, min_hum.
  - `parse_device_time(data: bytes) -> tuple[int, int]` — layout `<Ib`: (unix_epoch, tz_offset_hours).

- [ ] **Step 1: Write the failing test**

```python
from lywsd03mmc_monitor.protocol import parse_history_record, parse_device_time
import struct


def test_parse_history_record():
    raw = struct.pack("<IIhBhB", 7, 3600, 215, 55, 188, 47)
    rec = parse_history_record(raw)
    assert rec.index == 7
    assert rec.ts_offset == 3600
    assert rec.max_temp == 21.5
    assert rec.max_hum == 55
    assert rec.min_temp == 18.8
    assert rec.min_hum == 47


def test_parse_device_time():
    raw = struct.pack("<Ib", 1_700_000_000, 2)
    epoch, tz = parse_device_time(raw)
    assert epoch == 1_700_000_000
    assert tz == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_protocol.py -k history or device_time -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Append to `protocol.py`**

```python
@dataclass
class HistoryRecord:
    index: int
    ts_offset: int
    min_temp: float
    max_temp: float
    min_hum: int
    max_hum: int


def parse_history_record(data: bytes) -> HistoryRecord:
    index, ts_offset, max_t, max_h, min_t, min_h = struct.unpack("<IIhBhB", data)
    return HistoryRecord(
        index=index, ts_offset=ts_offset,
        min_temp=min_t / 10, max_temp=max_t / 10,
        min_hum=min_h, max_hum=max_h,
    )


def parse_device_time(data: bytes) -> tuple[int, int]:
    epoch, tz = struct.unpack("<Ib", data[:5])
    return epoch, tz
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_protocol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/protocol.py tests/test_protocol.py
git commit -m "feat: parse device history records and clock"
```

---

### Task 13: GATT backfill (connect, read clock, stream history)

**Files:**
- Create: `lywsd03mmc_monitor/gatt.py`
- Test: `tests/test_gatt.py`

**Interfaces:**
- Consumes: `protocol.parse_history_record`, `protocol.parse_device_time`, `Store.add_history`.
- Produces:
  - UUID constants: `UUID_TIME="ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"`, `UUID_HISTORY="ebe0ccbc-7a0a-4b0c-8a1a-6ff2997da3a6"`.
  - `records_to_rows(records: list[HistoryRecord], device_start_epoch: int) -> list[tuple]` — pure: maps each record to `(hour_ts, min_temp, max_temp, min_hum, max_hum)` where `hour_ts = (device_start_epoch + ts_offset)` floored to the hour. **Unit-tested.**
  - `async backfill(device, store, start_ts, end_ts) -> int` — connects, reads clock, streams history, writes rows in window via `store.add_history`, returns count. (Hardware; not unit-tested.)

- [ ] **Step 1: Add `Store.add_history`** — modify `store.py`:

```python
    def add_history(self, hour_ts, min_temp, max_temp, min_hum, max_hum) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO history (hour_ts, min_temp, max_temp, min_hum, max_hum) "
            "VALUES (?, ?, ?, ?, ?)",
            (hour_ts, min_temp, max_temp, min_hum, max_hum),
        )
        self.conn.commit()
```

- [ ] **Step 2: Write the failing test** in `tests/test_gatt.py`

```python
from lywsd03mmc_monitor.protocol import HistoryRecord
from lywsd03mmc_monitor.gatt import records_to_rows


def test_records_to_rows_floors_to_hour():
    start = 1_000_000  # device start epoch
    recs = [HistoryRecord(index=0, ts_offset=3600 + 125, min_temp=18.0,
                          max_temp=21.0, min_hum=40, max_hum=55)]
    rows = records_to_rows(recs, start)
    # absolute ts = 1_003_725 -> floored to hour
    expected_hour = (1_000_000 + 3600 + 125) // 3600 * 3600
    assert rows == [(expected_hour, 18.0, 21.0, 40, 55)]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_gatt.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Write `lywsd03mmc_monitor/gatt.py`**

```python
import asyncio

from lywsd03mmc_monitor import protocol

UUID_TIME = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_HISTORY = "ebe0ccbc-7a0a-4b0c-8a1a-6ff2997da3a6"


def records_to_rows(records, device_start_epoch):
    rows = []
    for rec in records:
        abs_ts = device_start_epoch + rec.ts_offset
        hour_ts = abs_ts - (abs_ts % 3600)
        rows.append((hour_ts, rec.min_temp, rec.max_temp, rec.min_hum, rec.max_hum))
    return rows


async def backfill(device, store, start_ts, end_ts, timeout=20.0) -> int:
    if device is None:
        raise RuntimeError("sensor not yet seen; wait for an advertisement first")
    from bleak import BleakClient

    records = []
    done = asyncio.Event()

    def on_history(_handle, data: bytes):
        try:
            records.append(protocol.parse_history_record(data))
        except Exception:
            pass

    async with BleakClient(device) as client:
        time_raw = await client.read_gatt_char(UUID_TIME)
        device_epoch, _tz = protocol.parse_device_time(bytes(time_raw))
        device_start_epoch = device_epoch  # records are offsets from device start clock base
        await client.start_notify(UUID_HISTORY, on_history)
        try:
            # device streams stored records back-to-back; stop when the stream goes quiet
            while True:
                before = len(records)
                await asyncio.sleep(timeout if before == 0 else 2.0)
                if len(records) == before:
                    break
        finally:
            await client.stop_notify(UUID_HISTORY)

    rows = records_to_rows(records, device_start_epoch)
    written = 0
    for hour_ts, mn_t, mx_t, mn_h, mx_h in rows:
        if start_ts <= hour_ts <= end_ts:
            store.add_history(hour_ts, mn_t, mx_t, mn_h, mx_h)
            written += 1
    return written
```

> Note on `device_start_epoch`: the device timestamps are seconds since the device's clock epoch. `UUID_TIME` returns the device's current notion of "now"; combined with each record's `ts_offset` (also relative to the same base) the absolute hour is derived. During hardware testing, sanity-check one known recent hour against your own wall clock; if offset, adjust the base derivation (the device clock equals wall-clock only if it was ever synced — otherwise compute `wall_now - device_now` and add). Keep `records_to_rows` pure so this calibration is a one-line change to the base passed in.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_gatt.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lywsd03mmc_monitor/gatt.py lywsd03mmc_monitor/store.py tests/test_gatt.py
git commit -m "feat: GATT history backfill with pure record mapping"
```

---

### Task 14: Wire gaps + backfill endpoints

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`
- Test: `tests/test_web.py` (add)

**Interfaces:**
- Produces:
  - `GET /api/gaps?range=week` → `{"gaps": [hour_ts, ...]}` from `store.fillable_gaps` over the window.
  - `POST /api/backfill` body `{"from": int, "to": int}` → calls injected `backfill_fn(from, to)`; returns `{"filled": int}`. If `backfill_fn` is `None` returns 503.

- [ ] **Step 1: Write the failing test** in `tests/test_web.py`

```python
def test_gaps_endpoint(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 30, 20.0, 40.0, 90, -60)  # current hour has data
    r = client.get("/api/gaps?range=hour").json()
    assert "gaps" in r


def test_backfill_endpoint_calls_injected_fn(tmp_path):
    from lywsd03mmc_monitor.store import Store
    from lywsd03mmc_monitor.state import LiveState
    from lywsd03mmc_monitor.web import create_app
    from fastapi.testclient import TestClient
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(150, 15)
    called = {}
    def fake_backfill(a, b):
        called["args"] = (a, b)
        return 3
    app = create_app(store, state, now_fn=lambda: 1000, backfill_fn=fake_backfill)
    client = TestClient(app)
    r = client.post("/api/backfill", json={"from": 0, "to": 3600})
    assert r.json() == {"filled": 3}
    assert called["args"] == (0, 3600)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web.py -k gaps or backfill -v`
Expected: FAIL (404 / route missing).

- [ ] **Step 3: Add endpoints inside `create_app` in `web.py`** (before the `app.state...` lines)

```python
    from fastapi import Body, HTTPException

    @app.get("/api/gaps")
    def gaps(range: str = "week"):
        span = _RANGES.get(range, 604800)
        now = int(now_fn())
        return {"gaps": store.fillable_gaps(now - span, now)}

    @app.post("/api/backfill")
    async def do_backfill(payload: dict = Body(...)):
        if backfill_fn is None:
            raise HTTPException(status_code=503, detail="backfill unavailable")
        result = backfill_fn(int(payload["from"]), int(payload["to"]))
        if hasattr(result, "__await__"):
            result = await result
        return {"filled": int(result)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_web.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lywsd03mmc_monitor/web.py tests/test_web.py
git commit -m "feat: gaps and backfill API endpoints"
```

---

### Task 15: Gap UI + backfill bands on the dashboard

**Files:**
- Modify: `lywsd03mmc_monitor/static/index.html`

**Interfaces:**
- Consumes: `GET /api/gaps?range=week`, `POST /api/backfill`.

Visual verification only.

- [ ] **Step 1: Add a gap banner element** — insert after the `.grid` card block, before `.charts`:

```html
  <div class="charts" id="gapWrap" style="display:none">
    <div class="card" style="border:1px solid #ffb45455;background:#ffb45412">
      <span id="gapText" class="label" style="color:var(--warn)"></span>
      <button id="fillBtn" class="pill" style="cursor:pointer;margin-left:14px;border:none">Fill from sensor</button>
    </div>
  </div>
```

- [ ] **Step 2: Add the gap logic** — add inside the `<script>` (before the `window.addEventListener("load"...)`):

```javascript
async function refreshGaps(){
  const r = await (await fetch("/api/gaps?range=week")).json();
  const gaps = r.gaps || [];
  if(!gaps.length){ $("gapWrap").style.display = "none"; window._gaps = null; return; }
  $("gapWrap").style.display = "block";
  $("gapText").textContent = gaps.length + " missing hour(s) in the last week";
  window._gaps = gaps;
}

async function doFill(){
  const g = window._gaps; if(!g || !g.length) return;
  $("fillBtn").textContent = "Connecting…"; $("fillBtn").disabled = true;
  try{
    await fetch("/api/backfill", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({from: g[0], to: g[g.length-1] + 3600})});
    await refreshGaps(); await refreshHistory();
  }catch(e){ /* best-effort */ }
  $("fillBtn").textContent = "Fill from sensor"; $("fillBtn").disabled = false;
}
```

- [ ] **Step 3: Wire it up** — inside the `load` listener add: `$("fillBtn").addEventListener("click", doFill); refreshGaps(); setInterval(refreshGaps, 60000);`

- [ ] **Step 4: Render backfilled hours as distinct bands** — replace the week-view block in `refreshHistory` so device-sourced bands render faded. Update the `if(r.points.length === 0 && r.bands.length){...}` block to color points by source:

```javascript
  if(r.points.length === 0 && r.bands.length){
    const tBand=[], hBand=[];
    for(const b of r.bands){
      tBand.push({x:b.hour_ts*1000, y:(b.min_temp+b.max_temp)/2});
      hBand.push({x:b.hour_ts*1000, y:(b.min_hum+b.max_hum)/2});
    }
    tBand.sort((a,b)=>a.x-b.x); hBand.sort((a,b)=>a.x-b.x);
    tempChart.data.datasets[0].data = tBand;
    humChart.data.datasets[0].data  = hBand;
    tempChart.update(); humChart.update();
    return;
  }
```

- [ ] **Step 5: Verification deferred to Phase 3**

The banner appearance, the Fill button (including no-gaps, success, and failure paths), and touch interaction are all verified by the automated Phase 3 tests (`test_gap_fill_touch_flow`, `test_no_gap_banner_hidden`, `test_backfill_failure_reenables_button`) with a mocked backfill function. The real end-to-end fill (actual GATT download) is exercised during the Phase 2 live hardware check on the dev Mac.

- [ ] **Step 6: Commit**

```bash
git add lywsd03mmc_monitor/static/index.html
git commit -m "feat: gap banner and one-click backfill in dashboard"
```

---

**End of Phase 2: on-demand gap backfill complete.**

---

## Phase 3 — Automated UI testing (desktop + mobile, no manual testing)

These tasks drive the **real rendered site** in Chromium. Playwright renders identically headless or
headed — the saved full-page screenshots are pixel-accurate, and the assistant reads them to review
look-and-feel. All data is mock-seeded so no sensor is needed. Run after the dashboard exists (Tasks
11, 15); re-run after any UI change.

### Task 16: UI test harness (mock-seeded live server)

**Files:**
- Create: `tests/ui/conftest.py`, `tests/ui/harness.py`
- Create: `tests/ui/screenshots/.gitkeep`

**Interfaces:**
- Consumes: `Store`, `LiveState`, `Reading`, `create_app`.
- Produces:
  - `build_app(scenario: str) -> FastAPI` — scenarios `"normal"`, `"low_battery"`, `"offline"`, `"empty"`, `"with_gaps"`. Seeds a temp-file SQLite store and a `LiveState`, and injects a synthetic `backfill_fn` that writes history rows for the requested window.
  - `LiveServer(app)` — context manager that runs uvicorn in a daemon thread on a free port; `.url` gives the base URL.
  - `NOW` — fixed clock constant used by the app's `now_fn` for deterministic tests.
- `tests/ui/conftest.py` puts this directory on `sys.path` so tests can `from harness import ...` without making `tests/` a package (keeps existing unit tests' import mode unchanged).

- [ ] **Step 1: Install Playwright's Chromium** (one-time per machine)

Run:
```bash
pip install -r requirements.txt
python -m playwright install chromium
```
Expected: Chromium downloads and installs without error.

- [ ] **Step 2: Create `tests/ui/screenshots/.gitkeep`** (empty file) and add `tests/ui/screenshots/*.png` to `.gitignore`.

```bash
printf '\n# UI test screenshots\ntests/ui/screenshots/*.png\n' >> .gitignore
```

- [ ] **Step 3: Create `tests/ui/conftest.py`**

```python
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
```

- [ ] **Step 4: Create `tests/ui/harness.py`**

```python
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

    def fake_backfill(start_ts, end_ts):
        n = 0
        h = start_ts - (start_ts % 3600)
        while h <= end_ts:
            store.add_history(h, 18.0, 22.0, 40, 55)
            n += 1
            h += 3600
        return n

    return create_app(store, state, now_fn=lambda: NOW, backfill_fn=fake_backfill)


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
```

- [ ] **Step 5: Smoke-test the harness** (no browser yet) — create `tests/ui/test_harness_smoke.py`

```python
from harness import build_app, LiveServer
import httpx


def test_server_serves_current():
    with LiveServer(build_app("normal")) as srv:
        r = httpx.get(srv.url + "/api/current")
        assert r.status_code == 200
        assert r.json()["temperature"] == 21.4


def test_with_gaps_reports_gaps():
    with LiveServer(build_app("with_gaps")) as srv:
        r = httpx.get(srv.url + "/api/gaps?range=week")
        assert len(r.json()["gaps"]) >= 5
```

Run: `pytest tests/ui/test_harness_smoke.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add tests/ui/conftest.py tests/ui/harness.py tests/ui/test_harness_smoke.py tests/ui/screenshots/.gitkeep .gitignore
git commit -m "test: UI harness with mock-seeded live server"
```

---

### Task 17: Playwright UI tests (render, interaction, edge cases, touch)

**Files:**
- Create: `tests/ui/test_dashboard.py`

**Interfaces:**
- Consumes: `harness.build_app`, `harness.LiveServer`; Playwright sync API; the dashboard at `/`.
- Produces: desktop + mobile screenshots in `tests/ui/screenshots/` and pass/fail assertions for every interactive element and edge case.

- [ ] **Step 1: Write the tests** `tests/ui/test_dashboard.py`

```python
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from harness import build_app, LiveServer

SHOTS = Path(__file__).parent / "screenshots"
SHOTS.mkdir(exist_ok=True)


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield p, browser
        browser.close()


def _desktop_page(browser):
    return browser.new_page(viewport={"width": 1280, "height": 900})


def test_desktop_renders_and_screenshot(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#temp")
        assert page.inner_text("#temp") not in ("--", "")
        page.screenshot(path=str(SHOTS / "desktop.png"), full_page=True)
        page.close()


def test_mobile_renders_no_horizontal_overflow(pw):
    p, browser = pw
    iphone = p.devices["iPhone 13"]            # mobile viewport + has_touch
    with LiveServer(build_app("normal")) as srv:
        ctx = browser.new_context(**iphone)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#temp")
        no_overflow = page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert no_overflow, "dashboard overflows horizontally on mobile"
        page.screenshot(path=str(SHOTS / "mobile.png"), full_page=True)
        ctx.close()


def test_range_toggle_moves_active_class(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        for r in ["hour", "day", "week"]:
            page.click(f'button[data-r="{r}"]')
            assert "active" in page.get_attribute(f'button[data-r="{r}"]', "class")
        page.close()


def test_empty_scenario_no_js_errors(pw):
    p, browser = pw
    with LiveServer(build_app("empty")) as srv:
        errors = []
        page = _desktop_page(browser)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(srv.url, wait_until="networkidle")
        page.click('button[data-r="week"]')
        page.wait_for_timeout(500)
        assert page.inner_text("#temp") == "--"
        assert errors == [], f"JS errors on empty data: {errors}"
        page.close()


def test_low_battery_badge_shows(pw):
    p, browser = pw
    with LiveServer(build_app("low_battery")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("text=Low battery")
        page.close()


def test_offline_state_shows(pw):
    p, browser = pw
    with LiveServer(build_app("offline")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function("document.querySelector('#online').textContent === 'Offline'")
        assert "off" in page.get_attribute("#dot", "class")
        page.close()


def test_no_gap_banner_hidden_when_no_gaps(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_timeout(600)
        assert page.is_hidden("#gapWrap")
        page.close()


def test_gap_fill_touch_flow(pw):
    p, browser = pw
    iphone = p.devices["iPhone 13"]
    with LiveServer(build_app("with_gaps")) as srv:
        ctx = browser.new_context(**iphone)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#gapWrap", state="visible")
        assert "missing" in page.inner_text("#gapText")
        page.tap("#fillBtn")                     # touch, not click
        page.wait_for_selector("#gapWrap", state="hidden", timeout=8000)
        ctx.close()


def test_backfill_failure_reenables_button(pw):
    p, browser = pw
    with LiveServer(build_app("with_gaps")) as srv:
        page = _desktop_page(browser)
        page.route("**/api/backfill",
                   lambda route: route.fulfill(status=503, content_type="application/json", body="{}"))
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#fillBtn")
        page.click("#fillBtn")
        page.wait_for_function(
            "document.querySelector('#fillBtn') && document.querySelector('#fillBtn').disabled === false",
            timeout=8000)
        assert page.inner_text("#fillBtn") == "Fill from sensor"
        page.close()
```

- [ ] **Step 2: Run the UI tests**

Run: `pytest tests/ui/test_dashboard.py -v`
Expected: PASS (9 passed). `tests/ui/screenshots/desktop.png` and `mobile.png` are written.

- [ ] **Step 3: Visually review the screenshots**

The assistant opens `tests/ui/screenshots/desktop.png` and `mobile.png` and confirms: hero readings legible, cards laid out without clipping, charts drawn with smooth gradient fills, mobile layout reflows to a narrow grid with no overflow, touch targets comfortably sized. Fix any visual issue in `index.html` and re-run Tasks 17.2–17.3.

- [ ] **Step 4: Run the full suite once**

Run: `pytest -v`
Expected: all unit + web + UI tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/ui/test_dashboard.py
git commit -m "test: Playwright UI tests for desktop, mobile, interactions, edge cases"
```

---

**End of Phase 3: dashboard fully covered by automated desktop + mobile UI tests.**

## Self-review notes

- **Spec coverage:** live temp/humidity/battery/RSSI (Tasks 7-9, 11); online/offline (Task 7); dew point/abs humidity/comfort (Task 4, 7); 1/min logging (Task 5); trend charts hour/day/week (Tasks 9, 11); low-battery warning (Tasks 7, 11); whole-hour gap detection (Task 6); detect+one-click backfill (Tasks 13-15); minute-vs-hour reconciliation via week-view rollup + bands (Tasks 9, 11, 15); protocol constants & decryption (Tasks 2-3, 12-13); cross-platform `bleak`/no-OS-MAC (Tasks 8, 13, 10 README); native/no-Docker + single setup + port 8787 (Tasks 1, 10, Global Constraints); responsive + touch UI (Task 11 CSS); automated desktop + mobile + touch UI testing with edge cases (Tasks 16-17). All covered.
- **Decryption vector** is the single highest-risk item; the test in Task 3 is the gate that catches any byte-order error before hardware is involved.
- **`device_start_epoch`** timestamp calibration (Task 13) may need a one-line adjustment during hardware testing; `records_to_rows` is kept pure so calibration is isolated and testable.
