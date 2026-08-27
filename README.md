# LYWSD03MMC Monitor

A small from-scratch app that monitors one Xiaomi LYWSD03MMC sensor over BLE and serves a dashboard.

## Features
- Listens to the sensor's encrypted BLE advertisements (needs the device **bindkey**) for live temperature, humidity, battery, and signal.
- Pulls the sensor's on-device history over GATT to fill in readings it missed while the app was off, with on-demand backfill of older data from the dashboard.
- Stores everything in a local SQLite database (`sensor.db`).
- Web dashboard (mobile-first): live readings with comfort/dew-point, plus two synced
  ECharts graphs (temperature, humidity) with 6H / 24H / 7D / 30D / All ranges,
  touch-scrub readout of exact values, min/max bands, and real gaps rendered as breaks.
  The server aggregates history into ≤ ~360 buckets per request, so every range —
  including All — loads completely in one small response.

## Setup
```
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml                   # then edit in your bindkey
```

## Run
```
python -m lywsd03mmc_monitor
```
Open http://127.0.0.1:8787  (default port; change it in config.toml)

## Tests
```
playwright install chromium         # one-time, needed by the UI tests
pytest                              # full suite: unit + API + dashboard (headless Chromium)
pytest --ignore=tests/ui            # skip the browser-based tests
```

## Deployment
`docs/deployment.md` documents running it unattended on an Ubuntu machine (systemd
user service, autostart at boot with no login, log rotation). `deploy/bootstrap.sh`
preps a fresh host, `deploy/push.sh` ships updates over SSH, and
`deploy/restore-db.sh` merges a backed-up `sensor.db` onto the target.

## Notes
- **macOS:** the first run prompts for Bluetooth permission for your terminal/app; allow it.
  The sensor's OS-level address is a CoreBluetooth UUID, not a MAC — this is expected and handled.
- **Linux:** BlueZ must be running and the user needs D-Bus access to `org.bluez`
  (usually the `bluetooth` group). Here `device.address` is the real MAC.
- **Windows:** no extra steps; Bluetooth must be on.
- The bindkey is generated when the sensor is paired in the Mi Home app. Extract it with
  [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor),
  which pulls it from your Xiaomi cloud account — browser-based flasher tricks no longer work
  on newer stock firmwares.
