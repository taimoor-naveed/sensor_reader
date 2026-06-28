from lywsd03mmc_monitor.store import Store


def test_add_reading_dedupes_to_minute(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(1000, 20.0, 40.0, 90, -60)   # minute floor 960
    s.add_reading(1010, 21.0, 41.0, 89, -61)   # same minute -> replaces
    rows = s.readings_between(0, 2000)
    assert len(rows) == 1
    assert rows[0]["ts"] == 960
    assert rows[0]["temperature"] == 21.0
    assert rows[0]["battery"] == 89


def test_readings_between_orders_and_filters(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(60, 1.0, 10.0, 50, -50)
    s.add_reading(180, 2.0, 11.0, 50, -50)
    s.add_reading(360, 3.0, 12.0, 50, -50)
    rows = s.readings_between(120, 300)
    assert [r["ts"] for r in rows] == [180]


def test_fillable_gaps_finds_empty_hours(tmp_path):
    from lywsd03mmc_monitor.store import Store
    s = Store(str(tmp_path / "t.db"))
    h0, h1, h2 = 0, 3600, 7200
    s.add_reading(h0 + 60, 20.0, 40.0, 90, -60)   # hour 0 has data
    s.add_reading(h2 + 60, 22.0, 42.0, 90, -60)   # hour 2 has data
    # hour 1 (3600) is empty -> a gap
    assert s.fillable_gaps(0, 10800) == [3600]


def test_fillable_gaps_skips_hours_with_history(tmp_path):
    from lywsd03mmc_monitor.store import Store
    s = Store(str(tmp_path / "t.db"))
    s.conn.execute(
        "INSERT INTO history VALUES (3600, 18.0, 19.0, 40, 45)")
    s.conn.commit()
    assert s.fillable_gaps(0, 7200) == [0]  # hour 1 already backfilled


def test_boot_epoch_round_trips(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.get_device_boot_epoch() is None
    s.set_device_boot_epoch(1_700_000_000)
    assert s.get_device_boot_epoch() == 1_700_000_000
    s.set_device_boot_epoch(1_700_000_500)   # replace
    assert s.get_device_boot_epoch() == 1_700_000_500


def test_classify_gaps_splits_on_boot_epoch(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    # hours 0..4 are all empty -> all gaps; boot at hour 2 (7200). The device only
    # logs COMPLETED hours, so its first record is the hour AFTER boot -- the partial
    # boot hour itself is unrecoverable, and fillable starts one hour later.
    res = s.classify_gaps(0, 5 * 3600, boot_epoch=2 * 3600)
    assert res["unrecoverable"] == [0, 3600, 7200]
    assert res["fillable"] == [10800, 14400]


def test_classify_gaps_none_boot_all_fillable(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    res = s.classify_gaps(0, 2 * 3600, boot_epoch=None)
    assert res["unrecoverable"] == []
    assert res["fillable"] == [0, 3600]


def test_classify_gaps_ignores_single_isolated_hour(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(60, 20.0, 40.0, 90, -60)            # hour 0 has data
    s.add_reading(2 * 3600 + 60, 21.0, 41.0, 90, -60)  # hour 2 has data; hour 1 isolated
    res = s.classify_gaps(0, 3 * 3600, boot_epoch=None)
    assert res["fillable"] == []   # a single 1-hour hole is not a fillable gap


def test_classify_gaps_reports_multi_hour_gap(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(60, 20.0, 40.0, 90, -60)            # hour 0
    s.add_reading(4 * 3600 + 60, 21.0, 41.0, 90, -60)  # hour 4; hours 1,2,3 missing (3h gap)
    res = s.classify_gaps(0, 5 * 3600, boot_epoch=None)
    assert res["fillable"] == [3600, 7200, 10800]


def test_today_high_low_picks_extremes_with_ts(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(100, 20.0, 40.0, 90, -60)
    s.add_reading(200, 25.5, 41.0, 90, -60)   # high
    s.add_reading(300, 18.2, 39.0, 90, -60)   # low
    res = s.today_high_low(0, 1000)
    assert res["high"] == 25.5 and res["high_ts"] == 180   # 200 floored to minute
    assert res["low"] == 18.2 and res["low_ts"] == 300


def test_today_high_low_empty(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    assert s.today_high_low(0, 1000) == {"high": None, "low": None,
                                         "high_ts": None, "low_ts": None}


def test_extent_spans_readings_and_history(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(5000, 20.0, 40.0, 90, -60)
    s.add_history(3600, 18.0, 22.0, 40, 55)   # earlier
    ext = s.extent()
    assert ext["earliest"] == 3600
    assert ext["latest"] == 4980   # 5000 floored to minute


def test_history_window_raw_vs_bands(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.add_reading(1000, 20.0, 40.0, 90, -60)
    raw = s.history_window(0, 3600)            # 1h span -> raw points
    assert raw["points"] and raw["bands"] == []
    s.add_reading(100, 19.0, 39.0, 90, -60)
    s.add_reading(200, 23.0, 44.0, 90, -60)
    wide = s.history_window(0, 3 * 86400)      # 3d span -> bands only
    assert wide["points"] == []
    assert any(b["min_temp"] == 19.0 and b["max_temp"] == 23.0 for b in wide["bands"])
