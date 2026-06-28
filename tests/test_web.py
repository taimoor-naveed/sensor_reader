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


def test_history_week_returns_raw_points(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 7200, 18.0, 30.0, 90, -60)
    store.add_reading(now - 7100, 22.0, 36.0, 90, -60)
    store.add_history(now - 3 * 3600, 10.0, 12.0, 40, 45)
    r = client.get("/api/history?range=week").json()
    # wide window now returns raw readings (frontend downsamples) plus device history
    assert any(p["temperature"] == 18.0 for p in r["points"])
    assert any(b["min_temp"] == 10.0 for b in r["bands"])
    assert r["extent"]["earliest"] is not None


def test_history_from_to_window_and_extent(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 600, 20.0, 40.0, 90, -60)
    r = client.get(f"/api/history?from={now-3600}&to={now}").json()
    assert any(p["temperature"] == 20.0 for p in r["points"])
    assert r["extent"]["earliest"] is not None
    assert r["extent"]["latest"] is not None


def test_gaps_endpoint_returns_split(tmp_path):
    now = 1_000_000
    store, state, client = _client(tmp_path, now=now)
    store.add_reading(now - 30, 20.0, 40.0, 90, -60)
    r = client.get("/api/gaps?range=hour").json()
    assert "fillable" in r and "unrecoverable" in r


def test_update_applies_reading_and_returns_snapshot(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    state = LiveState(150, 15)
    def fake_update():
        return {"temperature": 22.2, "humidity": 50, "voltage": 3.0,
                "battery": 90, "boot_epoch": 1_699_990_000}
    app = create_app(store, state, now_fn=lambda: 1_700_000_000, update_fn=fake_update)
    client = TestClient(app)
    body = client.post("/api/update").json()
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
