from lywsd03mmc_monitor.protocol import parse_advertisement, decrypt_payload, Reading, dew_point, absolute_humidity, comfort_band, parse_history_record, parse_device_time
import struct


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


def test_dew_point_at_saturation_equals_temperature():
    assert abs(dew_point(20.0, 100.0) - 20.0) < 0.1


def test_dew_point_realistic():
    assert abs(dew_point(27.2, 49.0) - 15.5) < 0.3


def test_absolute_humidity_realistic():
    assert abs(absolute_humidity(27.2, 49.0) - 12.8) < 0.3


def test_comfort_band():
    assert comfort_band(22.0, 45.0) == "comfortable"
    assert comfort_band(10.0, 45.0) == "cold"
    assert comfort_band(30.0, 45.0) == "hot"
    assert comfort_band(22.0, 20.0) == "dry"
    assert comfort_band(22.0, 70.0) == "humid"


def test_parse_history_record():
    raw = struct.pack("<IIhBhB", 7, 3600, 215, 55, 188, 47)
    rec = parse_history_record(raw)
    assert rec.index == 7
    assert rec.ts_offset == 3600
    assert rec.max_temp == 21.5
    assert rec.max_hum == 55
    assert rec.min_temp == 18.8
    assert rec.min_hum == 47


def test_parse_device_time():
    raw = struct.pack("<Ib", 1_700_000_000, 2)
    epoch, tz = parse_device_time(raw)
    assert epoch == 1_700_000_000
    assert tz == 2
