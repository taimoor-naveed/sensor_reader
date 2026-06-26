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
