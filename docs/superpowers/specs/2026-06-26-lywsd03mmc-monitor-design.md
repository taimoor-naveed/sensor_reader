# LYWSD03MMC Monitor — Design Spec

**Date:** 2026-06-26
**Status:** Approved, ready for implementation planning

## Purpose

A small, from-scratch full-stack app for a single Xiaomi **LYWSD03MMC** temperature/humidity
sensor. A long-running application listens to the sensor over Bluetooth LE, maintains current
and historical readings, and serves a modern web dashboard that visualizes the data.

Existing libraries (`Bluetooth-Devices/xiaomi-ble`, `uduncanu/lywsd03mmc`) target many sensors
and depend on Linux-only BLE stacks. We deliberately reimplement only the LYWSD03MMC-specific
protocol from scratch in a single focused, cross-platform codebase.

## Non-goals

- No support for sensors other than the LYWSD03MMC.
- No changing the sensor's display units (°C/°F).
- No general alerting (hot/cold/humidity thresholds). Only a low-battery warning.
- No setting the device clock (the firmware ignores it anyway).

## Constraints & environment

- **Develop on macOS** (Apple Silicon), **deploy most likely on Windows**; must be cross-platform.
- The one BLE library that works well on macOS (CoreBluetooth), Windows (WinRT), and Linux
  (BlueZ) behind one API — including passive advertisement scanning — is **`bleak`**. This is
  why the stack is Python rather than Node (`noble` has unreliable Windows support).
- The user has a valid **bindkey** for the sensor (16 bytes / 32 hex chars).
- macOS hides BLE MAC addresses, but the MAC needed for decryption is embedded *inside the
  advertisement payload*, so passive decryption works regardless of platform.

## Tech stack

- **`bleak`** — BLE scanning + GATT, cross-platform
- **`cryptography`** (`AESCCM`) — advertisement decryption
- **FastAPI + uvicorn** — JSON API and serving the page (async-native, coexists with `bleak`
  in one event loop)
- **SQLite** (stdlib `sqlite3`, WAL mode) — logged history
- **Frontend:** a single static HTML page with vanilla JS and a charting library **vendored
  locally** (no CDN, works offline). Modern, polished visual design with smooth/animated
  "flowing" line charts is an explicit requirement. Must be **responsive (desktop + mobile)
  with touch support** — touch-sized targets, `tap` interactions, no hover-only controls.
- **Playwright (Chromium)** — automated UI testing: real-browser full-page screenshots at
  desktop and mobile viewports plus interaction/touch tests against the app seeded with mock
  data. No manual UI testing.

## Deployment & runtime

- **Runs natively, not in Docker.** BLE cannot be reached from a container on either target
  platform: Docker Desktop on macOS and Windows runs Linux containers inside a VM with no access
  to the host Bluetooth radio (no CoreBluetooth/WinRT passthrough). Docker BLE only works on a
  native Linux host with `--privileged`, `--net=host`, and the host D-Bus/`bluetoothd` shared in —
  which neither target machine is. The app therefore runs as a native process.
- **One setup for dev and prod** — the same `pip install -r requirements.txt` + `python -m
  lywsd03mmc_monitor` on macOS (dev) and Windows (prod). No separate configurations.
- **Default port 8787** (configurable in `config.toml`), chosen to avoid an existing web server on
  the Windows machine.

## Architecture

One Python process, three layers, all in a single asyncio event loop:

```
┌─────────────────────────────────────────────────────┐
│  BLE layer        scanner.py  (passive, always on)    │
│                   gatt.py     (active, on-demand)     │
├─────────────────────────────────────────────────────┤
│  Core layer       protocol.py (pure decode/math)      │
│                   state.py    (live in-memory state)  │
│                   store.py    (SQLite + gap logic)    │
├─────────────────────────────────────────────────────┤
│  Web layer        web.py      (FastAPI JSON API)      │
│                   index.html  (dashboard + charts)    │
└─────────────────────────────────────────────────────┘
```

### Components

- **`protocol.py`** — pure functions, zero I/O, the correctness-critical core. Fully unit-testable
  offline against known vectors. Contains advertisement decode, history-record decode, and
  comfort math.
- **`scanner.py`** — `BleakScanner` with a detection callback; filters service UUID `0xFE95`,
  decodes via `protocol`, updates `state` and `store`. Tracks last-seen.
- **`gatt.py`** — on-demand only: connect, read device clock to anchor timestamps, stream the
  history characteristic, return hourly records for the gap window.
- **`store.py`** — SQLite. Dedupes live readings to one per minute, range queries, gap detection,
  backfill insertion.
- **`state.py`** — in-memory current reading + online/offline + battery status (single source of
  truth for the live view).
- **`web.py`** — FastAPI endpoints + serves the page.
- **`config.toml`** — sensor address, bindkey, battery-low threshold, offline threshold.
  Gitignored (contains the secret bindkey).

## Features

### Passive (always on, no connection)
- Live temperature (0.1 °C), humidity (1%), battery (%), RSSI.
- Online/offline freshness indicator ("live" vs "last seen N min ago") based on time since the
  last decoded advertisement.
- Derived comfort metrics: dew point, absolute humidity, comfort band.
- Logged history at one reading per minute → trend charts of temperature, humidity, and battery
  over hour / day / week.
- Low-battery warning badge on the dashboard.

### Active (on-demand GATT) — gap backfill only
- The app detects whole-hour gaps in the logged history and shows them on the dashboard with a
  **"Fill from sensor"** button (detect + one-click; connecting only happens when the user clicks).
- On click, connect to the sensor, download its stored hourly min/max history, and fill the
  missing hours.

## Data model (SQLite)

```sql
readings(ts INTEGER PRIMARY KEY,    -- unix seconds, minute-aligned
         temperature REAL,          -- °C, 0.1 resolution
         humidity REAL,             -- %
         battery INTEGER,           -- %
         rssi INTEGER)              -- dBm

history (hour_ts INTEGER PRIMARY KEY, -- unix seconds at top of hour
         min_temp REAL, max_temp REAL,
         min_hum INTEGER, max_hum INTEGER)  -- backfilled from device
```

Two tables on purpose: live data is an instantaneous point; device history is an hourly range.
Different shapes → keep them separate so every value always knows what it is. A year of live
data is ~525k tiny rows.

## Handling the minute-vs-hour granularity mismatch

We record live data every minute; the device stores only hourly min/max. Reconciliation rules:

1. **Backfill fills only whole empty clock-hours.** A sub-hour dropout can't be filled (device
   never stored it) and stays a small gap. A partially-covered hour keeps our live data and is
   **not** overlaid with the device record (our minute data is better).
2. **Gap detection is per clock-hour:** an hour with no live readings and no existing backfill is
   "fillable."
3. **Display reconciles by zoom level:**
   - **Hour / Day view:** live data as a minute-resolution line; backfilled hours as a faded
     min–max band (shaded region from min to max), visually distinct from live data.
   - **Week / Month view:** live minute data is rolled up to hourly min/max on the fly, so the
     whole chart is hourly min/max and device-backfilled hours slot in seamlessly. The mismatch
     disappears at the zoom level where it would otherwise matter.

## API

- `GET /` → dashboard
- `GET /api/current` → latest reading, derived metrics, online status, battery-warning flag
- `GET /api/history?range=hour|day|week` → merged series (live points + backfill bands)
- `GET /api/gaps` → fillable whole-hour gaps
- `POST /api/backfill?from=…&to=…` → connect, download, fill; returns what was filled

The dashboard polls `/api/current` every ~5 s for live values and loads `/api/history` for charts.

## Protocol reference (LYWSD03MMC specifics)

Reverse-engineered from the two repos; recorded here so implementation does not need to re-derive it.

### Advertisement (passive)
- Service Data UUID **`0xFE95`** (MiBeacon). Product ID **`0x055B`**.
- Service-data layout: `frctrl` (uint16 LE, bytes 0–1), product ID (uint16 LE, bytes 2–3),
  packet counter (byte 4), MAC (bytes 5–10, reversed, present if frame-control bit 4 set),
  optional capability byte(s) if bit 5 set, then payload.
- Frame-control bits (from the 16-bit LE value): version `>>12`, mesh `>>7 & 1`,
  object-include `>>6 & 1`, capability-include `>>5 & 1`, mac-include `>>4 & 1`,
  encrypted `>>3 & 1`.
- **Encrypted (v4/v5, version `>>12 >= 4`)**: AES-128-CCM, 4-byte MIC, `AAD = b"\x11"`.
  - `nonce = mac_LE(6) + data[2:5] + data[-7:-4]` (12 bytes)
  - `ciphertext = data[i:-7]`, `mic = data[-4:]` (where `i` = payload start)
  - `payload = AESCCM(bindkey, tag_length=4).decrypt(nonce, ciphertext + mic, b"\x11")`
  - `mac_LE` = the 6 MAC bytes in on-wire little-endian order.
- **Payload TLV objects:** `type` (uint16 LE), `len` (uint8), `value` (`len` bytes). Relevant:
  - `0x100D` — temp+humidity, 4 bytes, `<hH`, both `/10` (signed temp).
  - `0x1004` — temp, 2 bytes, `<h`, `/10`.
  - `0x1006` — humidity, 2 bytes, `<H`, `/10` (floor to int for this model).
  - `0x100A` — battery %, 1 byte (CR2032 voltage derivable as `2.2 + 0.9*(batt/100)`).
- The incrementing packet counter is folded into the nonce; we do not need counter-based dedup
  beyond optionally ignoring identical repeats.

### GATT (active, for backfill)
Vendor base UUID `…-7A0A-4B0C-8A1A-6FF2997DA3A6`:
- `UUID_TIME` `EBE0CCB7-…` — read device clock: `<Ib` = uint32 unix epoch + int8 tz-hours.
  Used to convert history's start-relative timestamps to wall-clock.
- `UUID_HISTORY` `EBE0CCBC-…` — notify: streams stored hourly records. Subscribe by writing
  `0x01` to the CCCD (descriptor `0x2902`). Record = 14 bytes `<IIhBhB`:
  index (uint32), timestamp (uint32, **seconds since device start**), max_temp (`/10`),
  max_hum, min_temp (`/10`), min_hum.
- `UUID_RECORD_IDX` `EBE0CCBA-…` — write uint32 to seek the history cursor (skip records we
  already have).
- (Live notification `UUID_DATA` `EBE0CCC1-…`, `<hBh` = temp/100, humidity, voltage/1000, and
  `UUID_NUM_RECORDS` `EBE0CCB9-…` exist but are not required for the gap-fill feature.)

History stop condition: stop when notifications time out or the latest record reaches the end of
the gap window. Connecting briefly interrupts advertising and uses battery, so it is on-demand only.

## Error handling

- Decrypt failures → skip + count; if persistent, surface "check bindkey" in the UI.
- **macOS Bluetooth permission** prompts on first run; documented in the README. Windows/BlueZ
  work without extra steps.
- GATT connect failure (out of range / busy) → retry twice, report gracefully; backfill is
  best-effort.
- SQLite in WAL mode, single writer.

## Testing

- `protocol.py` — unit tests against the known vectors (e.g. plaintext `50 30 5b 05 03 …` decodes
  to 27.2 °C / 49 %; an encrypted vector decrypts with its bindkey). No hardware needed.
- `store.py` — gap detection, minute dedupe, range queries with synthetic data.
- `state.py`, `config.py`, and the pure helpers in `scanner.py`/`gatt.py` (`handle_detection`,
  `records_to_rows`) — unit-tested with fakes; no hardware.
- **UI** — Playwright drives the real rendered site (seeded with mock data) at desktop and mobile
  (touch) viewports. Saves full-page screenshots for visual review and exercises every interactive
  element with edge cases: range toggle (incl. empty data), low-battery badge, online/offline
  state, gap banner + Fill button (no-gaps, success, failure). No manual UI testing.
- **Hardware** — the live BLE read against the real sensor is validated by running the app on the
  dev Mac (which has Bluetooth) during the build, not by the user.

## Build order

1. **Phase 1 (passive):** `protocol` → `scanner` → `store` → dashboard → charts → battery warning.
   A fully working app on its own.
2. **Phase 2 (active):** `gatt` backfill + gap detection/UI on top.
