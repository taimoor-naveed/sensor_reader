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


def test_rssi_to_bars_thresholds():
    from lywsd03mmc_monitor.state import rssi_to_bars
    assert rssi_to_bars(-50) == 4
    assert rssi_to_bars(-60) == 3
    assert rssi_to_bars(-75) == 2
    assert rssi_to_bars(-85) == 1
    assert rssi_to_bars(-95) == 0
    assert rssi_to_bars(None) == 0


def test_per_field_timestamps_only_set_for_present_fields():
    from lywsd03mmc_monitor.state import rssi_to_bars
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
