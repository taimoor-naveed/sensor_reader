from lywsd03mmc_monitor import protocol


class LiveState:
    def __init__(self, offline_after: float, battery_warn_below: int):
        self.offline_after = offline_after
        self.battery_warn_below = battery_warn_below
        self.temperature = None
        self.humidity = None
        self.battery = None
        self.rssi = None
        self.last_seen = None

    def update(self, reading, rssi, now) -> None:
        if reading.temperature is not None:
            self.temperature = reading.temperature
        if reading.humidity is not None:
            self.humidity = reading.humidity
        if reading.battery is not None:
            self.battery = reading.battery
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
        }
