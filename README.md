# LYWSD03MMC Monitor

A small from-scratch app that monitors one Xiaomi LYWSD03MMC sensor over BLE and serves a dashboard.

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

## Notes
- **macOS:** the first run prompts for Bluetooth permission for your terminal/app; allow it.
  The sensor's OS-level address is a CoreBluetooth UUID, not a MAC — this is expected and handled.
- **Windows/Linux:** no extra steps; Bluetooth must be on.
