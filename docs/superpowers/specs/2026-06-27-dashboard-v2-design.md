# LYWSD03MMC Dashboard v2 — Design

**Date:** 2026-06-27
**Status:** Approved (design finalized via visual brainstorming)
**Builds on:** v1 (`docs/superpowers/specs/2026-06-26-lywsd03mmc-monitor-design.md`), merged to `main`.

## Context

v1 is a passive BLE monitor + on-demand GATT history backfill + responsive dashboard
(42 tests, hardware-validated). This v2 round reworks the front-end and several behaviors.
Hardware quirks that drive the design are recorded in the `lywsd03mmc-hardware-findings` memory;
the ones that matter here:

- Passive advertisements send **one field per advert** (~every 10 s), staggered — a full
  temp+humidity+battery snapshot takes 30–60 s to populate.
- A GATT connection is **expensive for the sensor's battery**, so it is used **only on demand**.
- `DATA` char `EBE0CCC1` = 5-byte `<hBh` (temp/100, humidity, voltage/1000) — an exact combined
  live reading when connected.
- `HISTORY` char `EBE0CCBC` is notify-only; `NUMREC` `EBE0CCB9` = `<II` (total, current).
- `TIME` char `EBE0CCB7` = 4-byte uint32 uptime; device only stores hourly history **since last
  boot** (~2 days), so older gaps are **unrecoverable**.

## Goals

1. **Redesigned front-end** — the "Aurora" direction (refined dark theme).
2. **Live values feel current** within the battery constraint (passive-only; no persistent GATT).
3. **4-bar phone-style signal indicator** replacing the RSSI dBm number.
4. **Fluid interactive charts** — direct drag-pan + scroll/pinch-zoom, synced timeline, dynamic
   loading of the visible window.
5. **Backfill progress UX** — connecting → reading → progress bar (records received / total).
6. **Fix "still missing after fill"** — scope gap reporting to what's actually fillable; label
   unrecoverable gaps calmly.
7. **New "Update" button** — one-shot GATT read for an instant exact reading on demand.

### Non-goals
- No persistent/automatic GATT connection. No live `DATA` subscription while viewing.
- No SSE/WebSocket — browser uses light polling (decided: keep it simple).
- No multi-sensor support, auth, or alerting (unchanged from v1).

---

## 1. Live updates & freshness (battery-free path)

Live ambient values continue to come **only from passive advertisements** (the sensor broadcasts
regardless; zero added battery cost). We improve perceived liveness without a persistent connection:

- **Light polling** of `/api/current` every 2 s (down from 5 s). No SSE.
- **Per-field freshness:** the server tracks an independent "last updated" timestamp per field
  (temperature, humidity, battery). The UI shows "updated N s ago" under each value, so staggered
  passive updates read as honest rather than frozen.
- **Pulse animation:** when a field's value changes between polls, the UI briefly pulses it.

### State changes (`state.py`)
`LiveState` gains per-field timestamps and a battery source tag:

- `temperature_ts`, `humidity_ts`, `battery_ts` — set to `now` only when that field is present in
  an update.
- `battery_source` — `"advert"` or `"gatt"` (the latter is more accurate; see §2).
- `update(reading, rssi, now, source="advert")` sets only the fields present, stamping each.
- `snapshot(now)` adds: `temperature_ts`, `humidity_ts`, `battery_ts`, `battery_source`,
  `signal_bars` (0–4, see §3), `today_high`, `today_low`, `today_high_ts`, `today_low_ts`.
  (`today_*` are computed by `web.py` from the store, not held in `LiveState` — see §6.)

---

## 2. GATT actions (on-demand only — connect → do → disconnect)

Two user-triggered actions, both brief and non-persistent. A **single GATT mutex** serializes them
(only one BLE connection at a time); while one runs the other's control is disabled. Every GATT
connection also reads the `TIME` char and caches the device boot epoch (used by §5 gap
classification).

### 2a. Update — one-shot live read
- Connect → read `DATA` char (`EBE0CCC1`) → parse temp/humidity/voltage → disconnect.
- Derive battery % from voltage (CR2032 curve; more accurate than the passive % byte and the
  unreliable `BATTERY` char).
- Update `LiveState` (`source="gatt"`) and store the reading (`store.add_reading`) so it appears on
  the charts.
- Round trip ~2–4 s. Button states: `↻ Update` → `Connecting…` → `Reading…` → `✓ Updated`
  (values pulse, freshness resets to "just now"), then back to `↻ Update`.

### 2b. Fill from sensor — history backfill with progress
- Connect → read `NUMREC` (`EBE0CCB9`) for the expected total → subscribe to `HISTORY` notify →
  collect records until the stream goes idle → write rows in range → disconnect.
- Runs as an **async background task**; progress is exposed via a status object the frontend polls.
- This reuses the existing, hardware-validated `gatt.backfill` record→row logic (4-byte clock,
  `records_to_rows`); the change is wrapping it with progress reporting and the NUMREC total.

### Protocol additions (`protocol.py`)
- `UUID_DATA`, `UUID_NUMREC` constants.
- `parse_data(data: bytes) -> (temperature, humidity, voltage)` from `<hBh`.
- `parse_numrec(data: bytes) -> (total, current)` from `<II`.
- `voltage_to_percent(mv: int) -> int` — CR2032 curve, clamped 0–100 (e.g. linear 2100 mV→0%,
  3100 mV→100%; final curve chosen in implementation).

### GATT additions (`gatt.py`)
- `read_live(device) -> dict` — connect, read `DATA` (+ `TIME` to refresh boot epoch), disconnect;
  returns temp/humidity/voltage/battery%/boot_epoch.
- `backfill(...)` gains an optional `progress_cb(received, total)` and reads `NUMREC` for `total`.

---

## 3. Signal indicator (4 bars)

Replace the RSSI dBm number with a 4-bar indicator computed from the last advertisement RSSI.
Mapping (initial thresholds, tunable):

| RSSI (dBm)      | Bars |
|-----------------|------|
| ≥ −55           | 4    |
| −55 … −67       | 3    |
| −67 … −80       | 2    |
| −80 … −90       | 1    |
| < −90 or none   | 0    |

`snapshot` exposes `signal_bars`; the raw dBm stays available as a tooltip/title. Bars render as
four ascending pills; filled bars use the accent color, empty bars a muted color.

---

## 4. Interactive charts

Two **separate, stacked** charts — Temperature and Humidity — **never overlaid**. They share **one
synced timeline**: panning/zooming either updates both to the same window.

- **Library:** Chart.js (already vendored) + `chartjs-plugin-zoom` for pan/zoom, plus `hammer.js`
  for touch pinch. All vendored under `static/vendor/` (offline; CSP-safe). No CDN.
- **Interaction:** drag to pan, mouse-wheel / pinch to zoom. **No minimap/brush** (rejected —
  read like a video editor). The x-axis time labels re-scale and slide as the window changes.
- **Range chips:** `6h / 24h / 7d / All` set the window; free pan/zoom from there.
- **Synced timeline:** the zoom plugin's `onZoomComplete`/`onPanComplete` on one chart applies the
  same `min`/`max` to the other, then triggers a data fetch for the new window (debounced).
- **Dynamic loading:** a new `GET /api/history?from=<ts>&to=<ts>` returns the visible window.
  Granularity is chosen by span: **span ≤ 48 h** returns raw per-minute `readings`; **otherwise**
  hourly bands (`hourly_rollup` of live readings + device `history`). The current preset endpoint is
  generalized to from/to.

---

## 5. Gap handling — "Labeled" (Option B)

Fixes the "still missing after fill" bug by distinguishing **fillable** from **unrecoverable** gaps.

- **Fillable** = missing hours within the device's retained history range (≥ cached boot epoch).
- **Unrecoverable** = missing hours older than the boot epoch (or before the app first ran) — the
  device has no data for them.

### Banner
- Primary line: `⚠ N hours fillable` + **Fill from sensor** button (only when N > 0).
- Secondary calm note: `+ M earlier hours unavailable — beyond the sensor's ~2-day memory`
  (muted, non-alarming; dismissible). Shown only when M > 0.
- After a successful fill, the fillable count reaches 0 and the action/primary line disappears;
  only the quiet unrecoverable note may remain. **The banner never again claims fillable hours that
  cannot be filled** — this is the bug fix.

### Boot-epoch source
The split needs the device boot epoch, known only from a GATT `TIME` read. We **cache the most
recent boot epoch** (persisted in the store) and refresh it on every GATT connect (Update or Fill).
Before the first-ever connection, the boot epoch is unknown → treat all gaps as fillable
(attempting a fill will then establish it). Unrecoverable gaps still render as breaks in the chart
line regardless.

### Store changes (`store.py`)
- `classify_gaps(start, end, boot_epoch) -> {"fillable": [hour_ts...], "unrecoverable": [hour_ts...]}`
  — `fillable_gaps` split by the boot epoch.
- A small key/value table (or single-row meta table) to persist `device_boot_epoch`.

---

## 6. Backend API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET  | `/api/current` | snapshot + per-field timestamps, `battery_source`, `signal_bars`, `today_high/low(+ts)` |
| GET  | `/api/history?from=&to=` | points/bands for an arbitrary window (granularity by span); presets kept as from/to shortcuts |
| GET  | `/api/gaps?from=&to=` | `{ "fillable": [...], "unrecoverable": [...] }` |
| POST | `/api/update` | trigger one-shot GATT live read; returns the new snapshot |
| POST | `/api/backfill` | start async backfill of a range; returns `{ "started": true }` |
| GET  | `/api/backfill/status` | `{ "state": "idle\|connecting\|reading\|done\|error", "received": int, "total": int, "filled": int, "error": str\|null }` |

- `today_high/low`: `web.py` computes from `store` over the current **local** day window
  (readings + device history bands), returning value + timestamp.
- `/api/update` and `/api/backfill` share the GATT mutex; if busy, return `409` (frontend shows
  "sensor busy — try again").
- Backfill status is held in `app.state`; the background task updates it via the `progress_cb`.

---

## 7. Frontend architecture (`static/index.html`)

Single self-contained page (consistent with v1), Aurora theme. Structure:

- **Header:** room name + "LYWSD03MMC Monitor" sublabel · 4-bar signal · Live/Offline badge
  (the single source of online state) · `↻ Update` button.
- **Hero + tiles grid:** large gradient Temperature hero (2-row span, no inner sparkline,
  vertically centered, comfort pill + freshness) · Humidity · Battery (volts + source/freshness) ·
  Dew point (+ abs humidity) · Today (↑high ↓low with times). **No Status tile** (removed as
  redundant with the header badge). Each value carries its own "updated N s ago" + pulse.
- **Gap banner** (labeled, §5) — between tiles and charts, only when relevant.
- **Charts:** Temperature then Humidity, synced pan/zoom, range chips, "drag to pan · scroll/pinch
  to zoom" hint.

JS responsibilities: poll `/api/current` (2 s) and diff for pulses + freshness; range chips and
zoom/pan → fetch `/api/history?from&to` (debounced) and keep both charts' x-range in sync; `Update`
button → POST `/api/update` with the state machine; gap banner `Fill` → POST `/api/backfill` then
poll `/api/backfill/status` to drive the progress bar, then refresh charts + gaps.

### Aurora visual tokens
- Background: `radial-gradient(1100px 520px at 80% -12%, #16323f, #0e1420)`.
- Card: `linear-gradient(160deg,#17202c,#1b2533)`, `1px solid #ffffff10`, radius 18px.
- Hero number: gradient text `linear-gradient(120deg,#9be4ff,#c9b3ff)`.
- Accents: temp `#4cc9f0/#9be4ff`, humidity `#e879c9/#b5179e`, ok `#52d273`, warn `#ffb454`,
  muted `#5f7088/#7f90a8`.
- Final mockups: `.superpowers/brainstorm/.../aurora-dashboard-v4.html` (layout) and
  `backfill-gaps-flow.html` (flows).

---

## 8. Error handling

- GATT failures (Update/Fill): retain v1's retry logic; surface a brief inline error
  ("Couldn't reach sensor — try again"); never leave a spinner stuck. Status → `error`.
- `409` busy: friendly "sensor busy" message; re-enable when free.
- Backfill that returns 0 fillable rows when none expected: not an error; banner reflects reality.
- Network blips during polling: keep last values; don't blank the UI (v1 behavior).
- Unknown boot epoch: all gaps fillable; no crash; classification refines after first connect.

## 9. Testing

- **Unit (`protocol.py`):** `parse_data`, `parse_numrec`, `voltage_to_percent` (boundaries/clamp).
- **Unit (`state.py`):** per-field timestamps set only for present fields; `battery_source`;
  `signal_bars` thresholds (incl. boundaries and `None`).
- **Unit (`store.py`):** `classify_gaps` split around the boot epoch; today high/low queries;
  boot-epoch persistence.
- **Unit (`gatt.py`):** `read_live` mapping; `backfill` progress callback invoked with
  received/total (mock client).
- **API (`test_web.py`):** `/api/history?from&to` granularity; `/api/gaps` split shape;
  `/api/update` happy + busy(409); `/api/backfill` + `/api/backfill/status` progression; mutex.
- **UI (Playwright):** signal bars render; per-field freshness + pulse on change; Update state
  machine; backfill progress bar; labeled gap banner clears fillable after fill while keeping the
  unrecoverable note; pan/zoom keeps both charts synced. Extend the existing mock-seeded harness.

## 10. File-by-file change summary

- `protocol.py` — DATA/NUMREC parsers, voltage→%, new UUID constants.
- `state.py` — per-field timestamps, battery source, signal-bars helper, extended snapshot.
- `store.py` — `classify_gaps`, today high/low, boot-epoch persistence, `/api/history` granularity helpers.
- `gatt.py` — `read_live`, progress-reporting `backfill`, NUMREC read, boot-epoch surfacing.
- `web.py` — generalized `/api/history`, split `/api/gaps`, `/api/update`, async `/api/backfill` +
  `/api/backfill/status`, GATT mutex, today high/low in `/api/current`.
- `main.py` — wire the GATT mutex + update action into the app; pass background-task plumbing.
- `static/index.html` — full Aurora rebuild; vendor `chartjs-plugin-zoom` + `hammer.js`.
- `tests/` — extend unit, API, and UI suites as above.

## 11. Implementation order (high level)

1. Protocol + state + store units (pure, TDD).
2. GATT `read_live` + progress backfill (mock-tested).
3. Web API endpoints + mutex + status (API tests).
4. Frontend Aurora rebuild (static), then charts pan/zoom + sync, then flows.
5. UI tests; hardware validation pass on the real sensor.

Detailed task breakdown follows in the implementation plan.
