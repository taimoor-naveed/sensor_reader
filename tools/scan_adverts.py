#!/usr/bin/env python3
"""Watch a sensor's BLE advertisements by NAME, dump the raw payload, try to decode.

Scans continuously. For every advertisement whose device name matches NAME_MATCH it
prints everything in the packet (service data, manufacturer data, service UUIDs) and
then attempts to decrypt/decode the Xiaomi (0xFE95) reading.

Run from the repo root with the venv active:

    python tools/scan_adverts.py

Ctrl-C to stop.
"""
import asyncio
import os
import sys
from datetime import datetime

# Allow running directly as `python tools/scan_adverts.py`: add the repo root (the
# parent of tools/) to the import path so `lywsd03mmc_monitor` is found regardless of cwd.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from bleak import BleakScanner

from lywsd03mmc_monitor import protocol
from lywsd03mmc_monitor.config import load_config

NAME_MATCH = "LYWSD03MMC"   # case-insensitive substring of the advertised device name


def main(config_path: str = None) -> None:
    config_path = config_path or os.path.join(_REPO, "config.toml")
    bindkey = load_config(config_path).bindkey

    def on_advert(device, adv) -> None:
        name = (getattr(adv, "local_name", None) or getattr(device, "name", None) or "")
        if NAME_MATCH.lower() not in name.lower():
            return
        raw = (getattr(adv, "service_data", {}) or {}).get(protocol.SERVICE_UUID)
        if raw is None:
            return
        reading = protocol.parse_advertisement(bytes(raw), bindkey)
        if reading is None:
            return
        print(f"{datetime.now().strftime('%H:%M:%S')} {bytes(raw).hex()} {reading}", flush=True)

    async def run() -> None:
        scanner = BleakScanner(detection_callback=on_advert)
        await scanner.start()
        print(f"Scanning for devices named like {NAME_MATCH!r}… (Ctrl-C to stop)", flush=True)
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await scanner.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
