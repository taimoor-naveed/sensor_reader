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
