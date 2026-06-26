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
