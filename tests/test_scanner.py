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
