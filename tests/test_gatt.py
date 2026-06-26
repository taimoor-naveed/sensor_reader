from lywsd03mmc_monitor.protocol import HistoryRecord
from lywsd03mmc_monitor.gatt import records_to_rows
from lywsd03mmc_monitor.gatt import device_start_epoch


def test_records_to_rows_floors_to_hour():
    start = 1_000_000  # device start epoch
    recs = [HistoryRecord(index=0, ts_offset=3600 + 125, min_temp=18.0,
                          max_temp=21.0, min_hum=40, max_hum=55)]
    rows = records_to_rows(recs, start)
    # absolute ts = 1_003_725 -> floored to hour
    expected_hour = (1_000_000 + 3600 + 125) // 3600 * 3600
    assert rows == [(expected_hour, 18.0, 21.0, 40, 55)]


def test_device_start_epoch_subtracts_uptime():
    # device booted 7200s ago (uptime reported by UUID_TIME), host clock = 1_700_000_000
    assert device_start_epoch(7200, 1_700_000_000) == 1_700_000_000 - 7200


def test_records_to_rows_uses_boot_relative_base():
    host_now = 1_700_000_000
    base = device_start_epoch(7200, host_now)          # booted 7200s ago
    recs = [HistoryRecord(index=0, ts_offset=3600, min_temp=18.0,
                          max_temp=21.0, min_hum=40, max_hum=55)]
    abs_ts = (host_now - 7200) + 3600                  # record 1h into uptime
    expected_hour = abs_ts - (abs_ts % 3600)
    assert records_to_rows(recs, base) == [(expected_hour, 18.0, 21.0, 40, 55)]
    # sanity: the mapped hour must be recent (2026-ish), NOT 1970
    assert expected_hour > 1_600_000_000
