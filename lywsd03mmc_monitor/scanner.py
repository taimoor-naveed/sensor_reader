from lywsd03mmc_monitor import protocol


class Scanner:
    def __init__(self, bindkey, store, state, now_fn, address=None):
        self.bindkey = bindkey
        self.store = store
        self.state = state
        self.now_fn = now_fn
        self.address = address
        self.last_device = None
        self.last_counter = None
        self._bleak_scanner = None

    def handle_detection(self, device, advertisement_data) -> None:
        if self.address:
            dev_addr = getattr(device, "address", None)
            if dev_addr and dev_addr.lower() != self.address.lower():
                return
        service_data = getattr(advertisement_data, "service_data", {}) or {}
        raw = service_data.get(protocol.SERVICE_UUID)
        if raw is None:
            return
        raw = bytes(raw)
        reading = protocol.parse_advertisement(raw, self.bindkey)
        if reading is None:
            return
        self.last_device = device  # keep a fresh device handle for on-demand GATT
        counter = protocol.frame_counter(raw)
        if counter is not None and counter == self.last_counter:
            return  # re-delivered duplicate of the same packet — don't reset timers
        self.last_counter = counter
        now = self.now_fn()
        rssi = getattr(advertisement_data, "rssi", None)
        self.state.update(reading, rssi, now)
        self.store.add_reading(
            int(now), reading.temperature, reading.humidity,
            reading.battery, rssi,
        )

    async def start(self) -> None:
        from bleak import BleakScanner
        self._bleak_scanner = BleakScanner(detection_callback=self.handle_detection)
        await self._bleak_scanner.start()

    async def stop(self) -> None:
        if self._bleak_scanner is not None:
            await self._bleak_scanner.stop()
            self._bleak_scanner = None
