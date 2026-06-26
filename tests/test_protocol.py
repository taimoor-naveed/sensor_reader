from lywsd03mmc_monitor.protocol import parse_advertisement, decrypt_payload, Reading


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


def test_parse_truncated_mac_returns_none():
    # mac_include bit set (frctrl 0x3050) but frame too short to contain the MAC
    data = bytes.fromhex("50305b0503")
    assert parse_advertisement(data, b"\x00" * 16) is None


def test_decrypt_payload_known_vector():
    # service data bytes, bindkey, and expected plaintext from xiaomi-ble test vectors
    data = bytes.fromhex("5858e4162c84535638c1a42b6ef2e91200006c884d9e")
    bindkey = bytes.fromhex("a115210eed7a88e50ad52662e732a9fb")
    mac = data[5:11]            # 84 53 56 38 c1 a4
    payload_start = 11          # frctrl=0x5858 -> version5, mac included -> 5+6
    plaintext = decrypt_payload(data, mac, bindkey, payload_start)
    assert plaintext == bytes.fromhex("024c013a")  # obj 0x4C02, len 1, value 0x3a = 58


def test_decrypt_payload_wrong_key_returns_none():
    data = bytes.fromhex("5858e4162c84535638c1a42b6ef2e91200006c884d9e")
    assert decrypt_payload(data, data[5:11], b"\x00" * 16, 11) is None
