import asyncio
import time

from lywsd03mmc_monitor import protocol

UUID_TIME = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_HISTORY = "ebe0ccbc-7a0a-4b0c-8a1a-6ff2997da3a6"


def device_start_epoch(device_clock_seconds, host_now):
    """LYWSD03MMC has no real-time clock: UUID_TIME reports seconds since the
    device booted (encoded as a Unix epoch from 1970), and each history record's
    ts_offset is also measured from boot. So the wall-clock time the device booted
    is `host_now - device_clock_seconds`; a record's wall time is that base plus its
    ts_offset (added in records_to_rows)."""
    return host_now - device_clock_seconds


def records_to_rows(records, device_start_epoch):
    rows = []
    for rec in records:
        abs_ts = device_start_epoch + rec.ts_offset
        hour_ts = abs_ts - (abs_ts % 3600)
        rows.append((hour_ts, rec.min_temp, rec.max_temp, rec.min_hum, rec.max_hum))
    return rows


async def backfill(device, store, start_ts, end_ts, timeout=20.0, retries=2) -> int:
    if device is None:
        raise RuntimeError("sensor not yet seen; wait for an advertisement first")
    from bleak import BleakClient

    records = []

    def on_history(_handle, data: bytes):
        try:
            records.append(protocol.parse_history_record(data))
        except Exception:
            pass

    base = None
    for attempt in range(retries + 1):
        records.clear()
        try:
            async with BleakClient(device) as client:
                time_raw = await client.read_gatt_char(UUID_TIME)
                device_clock, _tz = protocol.parse_device_time(bytes(time_raw))
                base = device_start_epoch(device_clock, int(time.time()))
                await client.start_notify(UUID_HISTORY, on_history)
                try:
                    while True:
                        before = len(records)
                        await asyncio.sleep(timeout if before == 0 else 2.0)
                        if len(records) == before:
                            break
                finally:
                    await client.stop_notify(UUID_HISTORY)
            break  # connected and streamed successfully
        except Exception:
            if attempt == retries:
                raise

    rows = records_to_rows(records, base)
    written = 0
    for hour_ts, mn_t, mx_t, mn_h, mx_h in rows:
        if start_ts <= hour_ts <= end_ts:
            store.add_history(hour_ts, mn_t, mx_t, mn_h, mx_h)
            written += 1
    return written
