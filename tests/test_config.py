from pathlib import Path
from lywsd03mmc_monitor.config import load_config


def test_load_config_parses_bindkey_to_bytes(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[sensor]\n'
        'address = ""\n'
        'bindkey = "00112233445566778899aabbccddeeff"\n'
        '[app]\n'
        'host = "127.0.0.1"\n'
        'port = 8000\n'
        'offline_after_seconds = 150\n'
        'battery_warn_below = 15\n'
        'db_path = "sensor.db"\n'
    )
    cfg = load_config(str(cfg_file))
    assert cfg.bindkey == bytes.fromhex("00112233445566778899aabbccddeeff")
    assert len(cfg.bindkey) == 16
    assert cfg.address is None
    assert cfg.port == 8000
    assert cfg.battery_warn_below == 15


def test_load_config_rejects_bad_bindkey(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[sensor]\nbindkey = "1234"\n[app]\n'
        'host="127.0.0.1"\nport=8000\noffline_after_seconds=150\n'
        'battery_warn_below=15\ndb_path="sensor.db"\n'
    )
    import pytest
    with pytest.raises(ValueError):
        load_config(str(cfg_file))
