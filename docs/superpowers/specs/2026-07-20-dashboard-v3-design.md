# Dashboard v3 — graph rewrite + mobile-first polish

**Date:** 2026-07-20
**Driver:** the graph is the weakest part of the app. On mobile (the primary device) touch
feels janky, values can't be read, and — worst — wide ranges ("7d", "All") appear slow and
*missing data* until a zoom gesture forces a refetch. Owner picked: Health-app interaction
model, two separate synced charts, whole-dashboard mobile polish.

## Root cause of the "missing data" bug (v2)

`/api/history` returns **raw per-minute rows** for any requested window. The frontend keeps
one in-memory buffer that every fetch **replaces** (`ingest()` overwrites `memT/memH`), with
a debounced `ensure()` racing a 15-second auto-refresh that always re-fetches only 7 days.
Consequences: wide windows ship megabytes (slow), the buffer thrashes between windows, and
data outside the last-fetched window renders as *empty* until a gesture happens to trigger
`ensure()`. The whole client-side buffering scheme is unsalvageable complexity — v3 deletes
it.

## Design

### 1. Server-side aggregation (new `/api/history` contract)

The server owns downsampling. Every response is complete for its window and small
(≤ ~360 points), so the client never buffers, merges, or downsamples anything.

```
GET /api/history?range=6h|24h|7d|30d|all      (or ?from=&to= epoch seconds)
→ {
    "from": int, "to": int, "now": int,
    "bucket": int,                      # bucket width, seconds
    "extent": {"earliest": int|null, "latest": int|null},
    "series": [                         # one entry per bucket, t ascending, gaps = nulls
      {"t": int,                        # bucket start, epoch seconds
       "temp": float|null, "temp_min": float|null, "temp_max": float|null,
       "hum":  float|null, "hum_min":  float|null, "hum_max":  float|null}
    ]
  }
```

- **Bucket sizing:** smallest *nice* size ≥ span/360 from
  `{120, 300, 600, 1800, 3600, 10800, 21600, 43200, 86400}` (min 120 s tolerates the
  sensor's staggered per-field advertisements; averaging inside buckets also replaces v2's
  client-side smoothing hack — the 0.1 °C quantization noise averages out).
- **Aggregation:** `GROUP BY ts/bucket` over `readings` → avg/min/max per field
  (SQL `AVG/MIN/MAX` ignore NULLs from staggered adverts).
- **Device history fallback:** buckets with **no readings** are filled from the hourly
  `history` table (value = (min+max)/2, band = min..max). Readings win per bucket. For
  sub-hour buckets an hour's value spreads across its buckets (honest step look — that's
  the true resolution of the device history).
- **Gaps:** buckets with neither source → nulls → the chart breaks the line (no
  interpolation across real gaps).
- `range=all` uses the store extent. Empty DB → empty series, nulls in extent.

### 2. Frontend rebuilt from scratch on Apache ECharts

Chart.js + hammer.js + zoom plugin are removed. ECharts is what Home Assistant uses for
exactly this job; touch handling and axis-pointer scrubbing are built in.

- **Layout (mobile-first, single column):**
  1. Header: title, live/offline badge, signal bars, Update button.
  2. Stat cards: Temperature hero, Humidity, Battery, Today high/low + comfort/dew point
     (backend already computes `comfort` and `dew_point`; v2 never showed them).
  3. Range chips: `6H · 24H · 7D · 30D · ALL` (segmented control, sticky feel, 44 px touch
     targets).
  4. Temperature chart card, Humidity chart card — synced via `echarts.connect`.
  5. History-fetch (backfill) card at the bottom (occasional-use tool).
- **Interaction (Health-app model):** no free pan/zoom. Chips switch ranges (one fetch
  each). Touch-drag horizontally on a chart scrubs an axis pointer across both charts;
  `touch-action: pan-y` keeps vertical page scrolling native so the chart never traps the
  page. While scrubbing, each chart's card header becomes a readout: value + band + time;
  idle, it shows range summary (min · avg · max). Desktop hover behaves the same.
- **Series per chart:** avg line + subtle min/max band (stacked-line band recipe),
  `connectNulls: false`.
- **Refresh:** `/api/current` every 2 s (unchanged); active-range history refetch every
  60 s, paused while the page is hidden or the user is scrubbing.
- **Testability:** `window.__app` exposes charts + last history payload.

### 3. Backend fixes (from code review)

1. `scanner.py`: honor the configured `address` (currently accepted, never used).
2. `main.py`: BLE scanner failure must not kill the web app — log, serve anyway, retry the
   scanner in the background every 60 s.
3. `web.py`: hold a reference to the backfill task (`asyncio.create_task` fire-and-forget
   can be GC'd mid-flight).
4. `store.py`: guard the shared SQLite connection with a lock (scanner writes on the event
   loop; FastAPI reads on threadpool threads); set `busy_timeout`.
5. `store.py`: `fillable_gaps` does 2 queries per hour (~670/call for a week) → single-pass
   over two set queries.
6. Drop the dead `history_window/readings-as-JSON` payload (battery/rssi per point) from
   the API; `readings_between` stays for tests/tools.

`/api/current`, `/api/gaps`, `/api/update`, `/api/backfill` contracts are unchanged.

### 4. Testing

- **Unit/API:** bucket-size selection; aggregation (readings win, history fallback, null
  gaps, staggered-null AVG); new `/api/history` contract incl. `all` and empty DB;
  existing store/protocol/gatt tests stay.
- **Playwright (mobile-first, iPhone 13 emulation + desktop):** renders with data; no
  horizontal overflow; both charts populated (`window.__app`); range chips switch and
  refetch; touch scrub updates the readout; **regression: `ALL` spans the full extent
  immediately** (the v2 bug); empty DB → no JS errors; offline / low-battery badges;
  backfill touch flow; update button; screenshots.

### 5. Deployment

Vendor swap (`echarts.min.js` in, chart.js/hammer/zoom/adapter out) rides the normal
`deploy/push.sh` flow; no config or DB changes. Verify `/api/current` and a manual phone
check after push.

## Out of scope

Free pinch-zoom/pan, period paging (swipe to previous week), PWA manifest/icons, multiple
sensors.
