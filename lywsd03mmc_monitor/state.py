from lywsd03mmc_monitor import protocol


def rssi_to_bars(rssi) -> int:
    """Map advertisement RSSI (dBm) to a 0-4 phone-style signal level."""
    if rssi is None:
        return 0
    if rssi >= -55:
        return 4
    if rssi >= -67:
        return 3
    if rssi >= -80:
        return 2
    if rssi >= -90:
        return 1
    return 0


class LiveState:
    def __init__(self, offline_after: float, battery_warn_below: int):
        self.offline_after = offline_after
        self.battery_warn_below = battery_warn_below
        self.temperature = None
        self.humidity = None
        self.battery = None
        self.rssi = None
        self.last_seen = None
        self.temperature_ts = None
        self.humidity_ts = None
        self.battery_ts = None
        self.battery_source = None

    def update(self, reading, rssi, now, source="advert") -> None:
        if reading.temperature is not None:
            self.temperature = reading.temperature
            self.temperature_ts = now
        if reading.humidity is not None:
            self.humidity = reading.humidity
            self.humidity_ts = now
        if reading.battery is not None:
            self.battery = reading.battery
            self.battery_ts = now
            self.battery_source = source
        self.rssi = rssi
        self.last_seen = now

    def is_online(self, now) -> bool:
        return self.last_seen is not None and (now - self.last_seen) <= self.offline_after

    def battery_low(self) -> bool:
        return self.battery is not None and self.battery < self.battery_warn_below

    def snapshot(self, now) -> dict:
        dew = ah = comfort = None
        if self.temperature is not None and self.humidity is not None:
            dew = round(protocol.dew_point(self.temperature, self.humidity), 1)
            ah = round(protocol.absolute_humidity(self.temperature, self.humidity), 1)
            comfort = protocol.comfort_band(self.temperature, self.humidity)
        return {
            "temperature": self.temperature,
            "humidity": self.humidity,
            "battery": self.battery,
            "rssi": self.rssi,
            "last_seen": self.last_seen,
            "online": self.is_online(now),
            "battery_low": self.battery_low(),
            "dew_point": dew,
            "absolute_humidity": ah,
            "comfort": comfort,
            "temperature_ts": self.temperature_ts,
            "humidity_ts": self.humidity_ts,
            "battery_ts": self.battery_ts,
            "battery_source": self.battery_source,
            "signal_bars": rssi_to_bars(self.rssi),
        }
