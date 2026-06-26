import struct
from dataclasses import dataclass

SERVICE_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
PRODUCT_ID = 0x055B


@dataclass
class Reading:
    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None


def parse_advertisement(service_data: bytes, bindkey: bytes) -> Reading | None:
    if len(service_data) < 5:
        return None
    frctrl = service_data[0] | (service_data[1] << 8)
    product_id = service_data[2] | (service_data[3] << 8)
    if product_id != PRODUCT_ID:
        return None
    mesh = (frctrl >> 7) & 1
    obj_include = (frctrl >> 6) & 1
    cap_include = (frctrl >> 5) & 1
    mac_include = (frctrl >> 4) & 1
    is_encrypted = (frctrl >> 3) & 1
    if mesh or not obj_include:
        return None

    i = 5
    mac = None
    if mac_include:
        if len(service_data) < 11:
            return None
        mac = service_data[5:11]  # on-wire little-endian
        i += 6
    if cap_include:
        if i >= len(service_data):
            return None
        cap = service_data[i]
        i += 1
        if cap & 0x20:
            i += 1

    if is_encrypted:
        if mac is None:
            return None
        payload = decrypt_payload(service_data, mac, bindkey, i)
        if payload is None:
            return None
    else:
        payload = service_data[i:]

    return _parse_payload(payload)


def decrypt_payload(data: bytes, mac: bytes, bindkey: bytes, payload_start: int) -> bytes | None:
    return None  # implemented in Task 3


def _parse_payload(payload: bytes) -> Reading:
    reading = Reading()
    p = 0
    while p + 3 <= len(payload):
        obj_type = payload[p] | (payload[p + 1] << 8)
        length = payload[p + 2]
        value = payload[p + 3:p + 3 + length]
        if len(value) < length:
            break
        _apply_object(reading, obj_type, value)
        p += 3 + length
    return reading


def _apply_object(reading: Reading, obj_type: int, value: bytes) -> None:
    if obj_type == 0x100D and len(value) == 4:
        temp, humi = struct.unpack("<hH", value)
        reading.temperature = temp / 10
        reading.humidity = humi / 10
    elif obj_type == 0x1004 and len(value) == 2:
        reading.temperature = struct.unpack("<h", value)[0] / 10
    elif obj_type == 0x1006 and len(value) == 2:
        reading.humidity = int(struct.unpack("<H", value)[0] / 10)
    elif obj_type == 0x100A and len(value) == 1:
        reading.battery = value[0]
