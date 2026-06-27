import asyncio
import struct

import pytest

from lywsd03mmc_monitor import protocol
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


class _FakeClient:
    def __init__(self, chars):
        self._chars = chars
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def read_gatt_char(self, uuid):
        return self._chars[uuid]


def test_read_live_returns_reading_and_boot_epoch(monkeypatch):
    from lywsd03mmc_monitor.gatt import read_live, UUID_TIME
    chars = {
        protocol.UUID_DATA: struct.pack("<hBh", 2310, 47, 3010),  # 23.10C, 47%, 3.010V
        UUID_TIME: struct.pack("<I", 7200),                       # booted 7200s ago
    }
    monkeypatch.setattr("time.time", lambda: 1_700_000_000)
    res = asyncio.run(read_live(object(), client_factory=lambda d: _FakeClient(chars)))
    assert res["temperature"] == 23.1
    assert res["humidity"] == 47
    assert res["battery"] == protocol.voltage_to_percent(3.010)
    assert res["boot_epoch"] == 1_700_000_000 - 7200


def test_read_live_requires_device():
    from lywsd03mmc_monitor.gatt import read_live
    with pytest.raises(RuntimeError):
        asyncio.run(read_live(None))


class _FakeNotifyClient:
    def __init__(self, *, time_raw, numrec_raw, records):
        self._time = time_raw
        self._numrec = numrec_raw
        self._records = records
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def read_gatt_char(self, uuid):
        from lywsd03mmc_monitor.gatt import UUID_TIME
        return {UUID_TIME: self._time, protocol.UUID_NUMREC: self._numrec}[uuid]
    async def start_notify(self, uuid, cb):
        for rec in self._records:
            cb(0, rec)
    async def stop_notify(self, uuid):
        pass


def test_backfill_reports_progress_and_persists_boot(tmp_path, monkeypatch):
    from lywsd03mmc_monitor.gatt import backfill
    from lywsd03mmc_monitor.store import Store
    s = Store(str(tmp_path / "t.db"))
    host_now = 1_700_000_000
    monkeypatch.setattr("time.time", lambda: host_now)
    recs = [struct.pack("<IIhBhB", i, off, 220, 55, 180, 40)
            for i, off in [(0, 3600), (1, 7200)]]
    client = _FakeNotifyClient(time_raw=struct.pack("<I", 3 * 3600),
                               numrec_raw=struct.pack("<II", 2, 2), records=recs)
    seen = []
    written = asyncio.run(backfill(
        object(), s, 0, host_now, progress_cb=lambda r, t: seen.append((r, t)),
        timeout=0.01, poll=0.01, client_factory=lambda d: client))
    assert written == 2
    assert seen[-1] == (2, 2)            # received == total
    assert s.get_device_boot_epoch() == host_now - 3 * 3600
