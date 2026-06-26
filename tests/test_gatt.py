from lywsd03mmc_monitor.protocol import HistoryRecord
from lywsd03mmc_monitor.gatt import records_to_rows


def test_records_to_rows_floors_to_hour():
    start = 1_000_000  # device start epoch
    recs = [HistoryRecord(index=0, ts_offset=3600 + 125, min_temp=18.0,
                          max_temp=21.0, min_hum=40, max_hum=55)]
    rows = records_to_rows(recs, start)
    # absolute ts = 1_003_725 -> floored to hour
    expected_hour = (1_000_000 + 3600 + 125) // 3600 * 3600
    assert rows == [(expected_hour, 18.0, 21.0, 40, 55)]
