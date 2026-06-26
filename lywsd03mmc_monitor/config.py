import tomllib
from dataclasses import dataclass


@dataclass
class Config:
    bindkey: bytes
    address: str | None
    host: str
    port: int
    offline_after_seconds: float
    battery_warn_below: int
    db_path: str


def load_config(path: str) -> Config:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    sensor = data.get("sensor", {})
    app = data.get("app", {})
    bindkey = bytes.fromhex(sensor.get("bindkey", ""))
    if len(bindkey) != 16:
        raise ValueError("bindkey must be 32 hex chars (16 bytes)")
    address = sensor.get("address") or None
    return Config(
        bindkey=bindkey,
        address=address,
        host=app.get("host", "127.0.0.1"),
        port=int(app.get("port", 8787)),
        offline_after_seconds=float(app.get("offline_after_seconds", 150)),
        battery_warn_below=int(app.get("battery_warn_below", 15)),
        db_path=app.get("db_path", "sensor.db"),
    )
