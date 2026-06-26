from lywsd03mmc_monitor.protocol import parse_advertisement, Reading


def test_parse_plaintext_temp_humidity():
    # frctrl=0x3050 (v3, mac+obj, not encrypted), product 0x055B, obj 0x100D
    data = bytes.fromhex("50305b05034c94b438c1a40d10041001ea01")
    r = parse_advertisement(data, b"\x00" * 16)
    assert r is not None
    assert round(r.temperature, 1) == 27.2
    assert round(r.humidity, 1) == 49.0


def test_parse_rejects_wrong_product():
    # product 0x16e4 instead of 0x055B
    data = bytes.fromhex("5030e41603")
    assert parse_advertisement(data, b"\x00" * 16) is None
