# Dashboard v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the buggy client-side-buffered Chart.js graph with server-side aggregation + an ECharts mobile-first dashboard, and fix the backend issues found in review.

**Architecture:** `/api/history` returns a complete, bucket-aggregated series (≤ ~360 points) for any range, merging live readings with device hourly history and emitting nulls for gaps. The frontend becomes stateless per range: chip → one fetch → render two synced ECharts (avg line + min/max band), Health-app scrub readout, no client buffering.

**Tech Stack:** Python/FastAPI/SQLite (existing), Apache ECharts (vendored, replaces Chart.js/hammer/zoom/date-fns adapter), Playwright via pytest (existing harness).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-20-dashboard-v3-design.md`
- `/api/current`, `/api/gaps`, `/api/update`, `/api/backfill` contracts unchanged.
- Bucket sizes: smallest of `{120, 300, 600, 1800, 3600, 10800, 21600, 43200, 86400}` with `span/bucket ≤ 360`.
- Frontend keeps element ids used by ops flows: `#temp #hum #bat #updateBtn #fillBtn #gapWrap #fillProgressText #fillProgressBar #liveBadge #signalBars`.
- All temp/hum series nulls must render as line breaks (`connectNulls:false`), never interpolated.
- Mobile is primary: no horizontal page overflow at 390 px; touch targets ≥ 44 px; `touch-action: pan-y` on chart containers.

---

### Task 1: Store aggregation (`pick_bucket` + `aggregated_series`)

**Files:**
- Modify: `lywsd03mmc_monitor/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `pick_bucket(span_seconds: int, target: int = 360) -> int` (module-level, store.py); `Store.aggregated_series(start: int, end: int, bucket: int) -> list[dict]` — entries `{"t", "temp", "temp_min", "temp_max", "hum", "hum_min", "hum_max"}` for every bucket start in `[start - start%bucket, end]`, nulls where no data; readings win per field per bucket, hourly `history` fills the rest (`avg=(min+max)/2`).

- [ ] **Step 1: Write failing tests** (append to `tests/test_store.py`)

```python
def test_pick_bucket_sizes():
    from lywsd03mmc_monitor.store import pick_bucket
    assert pick_bucket(21600) == 120        # 6h -> 180 pts
    assert pick_bucket(86400) == 300        # 24h -> 288 pts
    assert pick_bucket(604800) == 1800      # 7d -> 336 pts
    assert pick_bucket(2592000) == 10800    # 30d -> 240 pts
    assert pick_bucket(365 * 86400) == 86400  # cap at 1d buckets

def test_aggregated_series_readings(store):
    base = 1_700_000_000 - 1_700_000_000 % 300
    store.add_reading(base + 0, 20.0, 40, 90, -60)
    store.add_reading(base + 60, 22.0, 44, 90, -60)
    out = store.aggregated_series(base, base + 300, 300)
    row = [r for r in out if r["t"] == base][0]
    assert row["temp"] == 21.0 and row["temp_min"] == 20.0 and row["temp_max"] == 22.0
    assert row["hum"] == 42.0

def test_aggregated_series_gap_is_null(store):
    base = 1_700_000_000 - 1_700_000_000 % 300
    store.add_reading(base, 20.0, 40, 90, -60)
    store.add_reading(base + 900, 21.0, 41, 90, -60)
    out = store.aggregated_series(base, base + 1200, 300)
    assert [r["temp"] for r in out][:4] == [20.0, None, None, 21.0]

def test_aggregated_series_history_fallback_subhour(store):
    hour = 1_700_000_000 - 1_700_000_000 % 3600
    store.add_history(hour, 18.0, 20.0, 40, 50)
    out = store.aggregated_series(hour, hour + 3600, 300)
    assert all(r["temp"] == 19.0 and r["temp_min"] == 18.0 for r in out[:-1])

def test_aggregated_series_history_fallback_coarse(store):
    hour = 1_700_000_000 - 1_700_000_000 % 10800
    store.add_history(hour, 18.0, 20.0, 40, 50)
    store.add_history(hour + 3600, 20.0, 22.0, 42, 52)
    out = store.aggregated_series(hour, hour + 10800, 10800)
    assert out[0]["temp"] == 20.0 and out[0]["temp_min"] == 18.0 and out[0]["temp_max"] == 22.0

def test_aggregated_series_readings_beat_history(store):
    hour = 1_700_000_000 - 1_700_000_000 % 3600
    store.add_history(hour, 10.0, 12.0, 10, 12)
    store.add_reading(hour + 60, 25.0, 55, 90, -60)
    out = store.aggregated_series(hour, hour + 3600, 3600)
    assert out[0]["temp"] == 25.0 and out[0]["hum"] == 55.0

def test_aggregated_series_staggered_fields(store):
    base = 1_700_000_000 - 1_700_000_000 % 300
    store.add_reading(base, 20.0, None, 90, -60)
    store.add_reading(base + 60, None, 44, 90, -60)
    out = store.aggregated_series(base, base + 300, 300)
    assert out[0]["temp"] == 20.0 and out[0]["hum"] == 44.0
```

(`store` fixture: whatever `tests/test_store.py` already uses; add one if missing:
`@pytest.fixture def store(tmp_path): return Store(str(tmp_path/"t.db"))`.)

- [ ] **Step 2:** `pytest tests/test_store.py -q` → new tests FAIL (no `pick_bucket`).
- [ ] **Step 3: Implement** in `store.py`:

```python
NICE_BUCKETS = [120, 300, 600, 1800, 3600, 10800, 21600, 43200, 86400]

def pick_bucket(span_seconds: int, target: int = 360) -> int:
    for b in NICE_BUCKETS:
        if span_seconds / b <= target:
            return b
    return NICE_BUCKETS[-1]
```

and `Store.aggregated_series`:

```python
def aggregated_series(self, start: int, end: int, bucket: int) -> list[dict]:
    first = start - (start % bucket)
    read = {}
    for r in self.conn.execute(
        "SELECT (ts/?)*? AS b, AVG(temperature), MIN(temperature), MAX(temperature), "
        "AVG(humidity), MIN(humidity), MAX(humidity) FROM readings "
        "WHERE ts >= ? AND ts <= ? GROUP BY b", (bucket, bucket, first, end)):
        read[r[0]] = r[1:]
    hist = {}
    for h in self.conn.execute(
        "SELECT hour_ts, min_temp, max_temp, min_hum, max_hum FROM history "
        "WHERE hour_ts >= ? AND hour_ts <= ?", (first - 3600, end)):
        hist[h[0]] = h[1:]
    hist_b = {}
    if bucket >= 3600:
        for hour_ts, (mnt, mxt, mnh, mxh) in hist.items():
            b = hour_ts - (hour_ts % bucket)
            a = hist_b.setdefault(b, [[], [], [], []])
            a[0].append((mnt + mxt) / 2); a[1].append(mnt); a[2].append(mxt)
            a[3].append(((mnh + mxh) / 2, mnh, mxh))
    out = []
    b = first
    while b <= end:
        t = tn = tx = hu = hn = hx = None
        if b in read:
            ta, tn0, tx0, ha, hn0, hx0 = read[b]
            t, tn, tx, hu, hn, hx = ta, tn0, tx0, ha, hn0, hx0
        if bucket >= 3600 and b in hist_b:
            acc = hist_b[b]
            if t is None and acc[0]:
                t, tn, tx = sum(acc[0]) / len(acc[0]), min(acc[1]), max(acc[2])
            if hu is None and acc[3]:
                hu = sum(v[0] for v in acc[3]) / len(acc[3])
                hn, hx = min(v[1] for v in acc[3]), max(v[2] for v in acc[3])
        elif bucket < 3600:
            hr = b - (b % 3600)
            if hr in hist:
                mnt, mxt, mnh, mxh = hist[hr]
                if t is None:
                    t, tn, tx = (mnt + mxt) / 2, mnt, mxt
                if hu is None:
                    hu, hn, hx = (mnh + mxh) / 2, mnh, mxh
        out.append({"t": b, "temp": t, "temp_min": tn, "temp_max": tx,
                    "hum": hu, "hum_min": hn, "hum_max": hx})
        b += bucket
    return out
```

- [ ] **Step 4:** `pytest tests/test_store.py -q` → PASS.
- [ ] **Step 5:** Commit `feat(store): bucketed aggregation for chart series`.

### Task 2: Backend robustness fixes

**Files:**
- Modify: `lywsd03mmc_monitor/store.py` (lock, busy_timeout, fillable_gaps), `lywsd03mmc_monitor/scanner.py` (address filter), `lywsd03mmc_monitor/web.py` (backfill task ref), `lywsd03mmc_monitor/main.py` (resilient scanner start)
- Test: `tests/test_scanner.py`, `tests/test_store.py` (existing gap tests must stay green)

**Interfaces:** unchanged public APIs.

- [ ] **Step 1: failing scanner test** (append to `tests/test_scanner.py`, mirroring its existing fake device/advert helpers):

```python
def test_ignores_other_address():
    # build scanner with address="AA:BB:..." and deliver a valid advert from a
    # device whose .address differs -> store stays empty, state not updated
```

- [ ] **Step 2:** scanner filter at the top of `handle_detection`:

```python
if self.address:
    dev_addr = getattr(device, "address", None)
    if dev_addr and dev_addr.lower() != self.address.lower():
        return
```

- [ ] **Step 3:** store lock: in `__init__` add `self._lock = threading.Lock()` and
`self.conn.execute("PRAGMA busy_timeout=5000")`; wrap every method that touches
`self.conn` in `with self._lock:`. Rewrite `fillable_gaps` as two set queries:

```python
def fillable_gaps(self, start: int, end: int) -> list[int]:
    first_hour = start - (start % 3600)
    with self._lock:
        have = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT (ts/3600)*3600 FROM readings WHERE ts >= ? AND ts < ?",
            (first_hour, end))}
        have |= {r[0] for r in self.conn.execute(
            "SELECT hour_ts FROM history WHERE hour_ts >= ? AND hour_ts < ?",
            (first_hour, end))}
    return [h for h in range(first_hour, end, 3600) if h not in have]
```

- [ ] **Step 4:** web.py: `app.state.backfill_task = asyncio.create_task(run())`.
- [ ] **Step 5:** main.py: replace `await scanner.start()` with a background task that
retries every 60 s and never crashes the server:

```python
async def _start_scanner(scanner):
    log = logging.getLogger("scanner")
    while True:
        try:
            await scanner.start()
            return
        except Exception:
            log.exception("BLE scanner failed to start; retrying in 60s")
            await asyncio.sleep(60)
...
scanner_task = asyncio.create_task(_start_scanner(scanner))
try:
    await server.serve()
finally:
    scanner_task.cancel()
    await scanner.stop()
```

- [ ] **Step 6:** `pytest --ignore=tests/ui -q` → PASS. Commit `fix: address filter, resilient BLE start, store lock, fast gap scan`.

### Task 3: New `/api/history` contract

**Files:**
- Modify: `lywsd03mmc_monitor/web.py`, `lywsd03mmc_monitor/store.py` (delete `history_window`)
- Test: `tests/test_web.py` (replace the three old history tests)

**Interfaces:**
- Produces: `GET /api/history` → `{range, from, to, now, bucket, extent, series}` per spec. Ranges: `6h, 24h, 7d, 30d, all` (+ legacy aliases `hour, day, week, month`).

- [ ] **Step 1: failing tests** (replace `test_history_day_returns_points`, `test_history_week_returns_raw_points`, `test_history_from_to_window_and_extent`):

```python
def test_history_returns_aggregated_series(tmp_path):
    now = 1_700_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 600, 20.0, 40.0, 90, -60)
    r = client.get("/api/history?range=24h").json()
    assert r["bucket"] == 300
    assert any(p["temp"] == 20.0 for p in r["series"])
    assert all(set(p) == {"t","temp","temp_min","temp_max","hum","hum_min","hum_max"}
               for p in r["series"])

def test_history_merges_device_history(tmp_path):
    now = 1_700_000_000
    store, state, client = _client(tmp_path, now=now)
    hour = (now - 7200) - ((now - 7200) % 3600)
    store.add_history(hour, 10.0, 12.0, 40, 45)
    r = client.get("/api/history?range=6h").json()
    assert any(p["temp"] == 11.0 for p in r["series"])

def test_history_all_uses_extent(tmp_path):
    now = 1_700_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 40 * 86400, 15.0, 30.0, 90, -60)
    store.add_reading(now - 60, 25.0, 50.0, 90, -60)
    r = client.get("/api/history?range=all").json()
    assert r["from"] <= now - 40 * 86400
    assert r["to"] == now
    assert len(r["series"]) <= 400

def test_history_empty_db(tmp_path):
    store, state, client = _client(tmp_path, now=1_700_000_000)
    r = client.get("/api/history?range=all").json()
    assert r["extent"]["earliest"] is None
    assert all(p["temp"] is None for p in r["series"])

def test_history_custom_window(tmp_path):
    now = 1_700_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 600, 20.0, 40.0, 90, -60)
    r = client.get(f"/api/history?from={now-3600}&to={now}").json()
    assert r["bucket"] == 120
    assert any(p["temp"] == 20.0 for p in r["series"])
```

- [ ] **Step 2: implement** in web.py:

```python
_RANGES = {"hour": 3600, "6h": 21600, "day": 86400, "24h": 86400,
           "7d": 604800, "week": 604800, "30d": 2592000, "month": 2592000}

@app.get("/api/history")
def history(range: str = None,
            start: int = Query(None, alias="from"),
            end: int = Query(None, alias="to")):
    now = int(now_fn())
    ext = store.extent()
    if start is None or end is None:
        if range == "all":
            start = ext["earliest"] if ext["earliest"] is not None else now - 86400
            end = now
        else:
            span = _RANGES.get(range or "day", 86400)
            start, end = now - span, now
    start, end = int(start), int(end)
    bucket = pick_bucket(max(1, end - start))
    return {"range": range or "custom", "from": start, "to": end, "now": now,
            "bucket": bucket, "extent": ext,
            "series": store.aggregated_series(start, end, bucket)}
```

Delete `Store.history_window`.

- [ ] **Step 3:** `pytest --ignore=tests/ui -q` → PASS. Commit `feat(api): /api/history returns complete bucket-aggregated series`.

### Task 4: Frontend rebuild (ECharts, mobile-first)

**Files:**
- Rewrite: `lywsd03mmc_monitor/static/index.html`
- Add: `lywsd03mmc_monitor/static/vendor/echarts.min.js` (jsdelivr `echarts@6` dist, exact version noted in commit)
- Delete: `static/vendor/{chart.umd.min.js, chartjs-adapter-date-fns.bundle.min.js, chartjs-plugin-zoom.min.js, hammer.min.js}`

**Interfaces:**
- Consumes: `/api/history` (Task 3), `/api/current`, `/api/gaps`, `/api/update`, `/api/backfill(+/status)`.
- Produces (for tests): `window.__app = {charts: {t, h}, state}` where `state = {token, data, scrubUntil}`; required ids per Global Constraints plus `#ranges` chips (`data-r` ∈ 6h|24h|7d|30d|all), `#tchart #hchart` chart divs, `#tread #hread` readouts.

Blueprint (key decisions locked; full markup/CSS written at implementation):

1. **Layout order:** header (title, `#liveBadge`, `#signalBars`, `#updateBtn`) → stat grid (temp hero w/ comfort pill + dew point, humidity, battery, today hi/lo) → `#ranges` segmented chips (flex, 44 px) → temperature card → humidity card → backfill card. Single column ≤ 480 px; 2-col stats + side-by-side charts ≥ 900 px.
2. **Charts:** `echarts.init(el, null, {renderer:'canvas'})`; per chart 3 series: `min` (stack "band", invisible), `band` (stack "band", `areaStyle` 12 % opacity), `avg` line (`connectNulls:false`, `showSymbol:false`); `xAxis:{type:'time', min:from*1000, max:to*1000}`; `yAxis:{scale:true}`; `tooltip:{trigger:'axis', showContent:false}` (gives the synced axis-pointer line); `animation:false`. `echarts.connect([t, h])`.
3. **Scrub readout:** on `updateAxisPointer`, binary-search nearest bucket, write `value + (min–max) + time` into `#tread`/`#hread`, set `state.scrubUntil = Date.now()+4000`; a 1 s ticker restores the range summary (`↓min ↑max · avg`) and `dispatchAction({type:'hideTip'})` after scrubUntil passes. CSS `touch-action: pan-y` on `#tchart/#hchart`.
4. **Data flow:** `loadRange(token)` = one fetch → `render()` maps `series` to `[t*1000, v]` triplets (band value = `max-min`), sets axis min/max, updates summaries. Refresh: current 2 s; history 60 s only when `!document.hidden && Date.now() > state.scrubUntil`; after Update/backfill success → `loadRange(state.token)`.
5. **Backfill/update cards:** same endpoints/ids as v2 (`doFetch`, `pollBackfill`, `doUpdate` logic carried over, restyled).

- [ ] **Step 1:** download echarts, delete old vendors.
- [ ] **Step 2:** write new `index.html` per blueprint.
- [ ] **Step 3:** manual smoke: run harness server, load page desktop + iPhone-13 emulation, check charts render, chips work, scrub works, no console errors.
- [ ] **Step 4:** Commit `feat(ui): dashboard v3 — ECharts mobile-first rebuild`.

### Task 5: UI test suite rewrite + full suite green

**Files:**
- Modify: `tests/ui/test_dashboard.py` (rewrite chart tests, keep scenario tests), `tests/ui/harness.py` (keep; seed unchanged)

Tests (mobile context = `p.devices["iPhone 13"]`):
- mobile renders, `#temp` populated, **no horizontal overflow**, screenshot `mobile.png`
- desktop renders, screenshot `desktop.png`
- charts populated: `__app.charts.t/.h` avg series has non-null points
- chips: click `7d` → `state.token==='7d'`, `state.data.bucket===1800`, active class moves
- **regression:** click `all` → `state.data.from <= NOW-167*3600` and >50 non-null temp buckets (seed spans 168 h) — immediately, no gesture
- fresh_deploy: 24h view non-null span ≥ 12 h (history fallback)
- mobile tap on `#tchart` → `#tread` shows a value readout (scrub)
- empty scenario: click every chip, no JS errors
- keep: offline badge, low-battery badge, update button cycle, backfill touch flow + clears gap + failure re-enables button, fetch card neutral without gaps, signal bars

- [ ] **Step 1:** rewrite tests; run `pytest tests/ui -q` → PASS.
- [ ] **Step 2:** run `pytest -q` (whole suite) → PASS.
- [ ] **Step 3:** Commit `test(ui): v3 chart interactions, mobile scrub, all-range regression`.

### Task 6: Docs

**Files:**
- Modify: `README.md` (dashboard feature bullet: ranges 6h/24h/7d/30d/All, scrub readout, server-side aggregation; note ECharts vendored)

- [ ] Update + commit `docs(readme): dashboard v3`.

### Task 7: Deploy + verify

- [ ] `deploy/push.sh` (uses `deploy/local.env`); expect `SUCCESS: app is serving`.
- [ ] `ssh <remote> curl http://127.0.0.1:8787/api/history?range=24h` → JSON with `bucket` key.
- [ ] Report to user with screenshots.

## Self-Review

- Spec coverage: aggregation (T1), API (T3), backend fixes (T2), frontend (T4), tests (T5), deploy (T7) ✓; `/api/gaps` etc. untouched ✓.
- Types consistent: `pick_bucket`/`aggregated_series` names used in T3 match T1 ✓; test ids match T4's produced ids ✓.
- Placeholders: T4 defers full markup by design (single-author inline execution); all load-bearing decisions are pinned in the blueprint.
