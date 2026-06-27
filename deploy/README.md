# Deploying to the Windows home PC

The app runs on **<host>** (`<user>@<host>`, Windows 10) as a background service that
autostarts at logon and serves the dashboard on the LAN at **http://<host>:8787**.

## Push code / updates

From the repo root on the Mac:

```bash
deploy/push.sh
# or target a different host/user:
REMOTE=<user>@<lan-ip> deploy/push.sh
```

Each run (first run bootstraps, later runs are incremental):

1. Packs the `lywsd03mmc_monitor` package + deploy helpers with `tar`, ships it over `scp`.
2. Stops the running instance, extracts the new code into `C:\Users\<user>\sensor_reader`.
3. Ensures the **virtual environment** (`.venv`) and installs `requirements-runtime.txt`.
4. (Re)registers the `sensor_reader` scheduled task and relaunches the app.
5. Waits until `GET /api/current` responds, then reports success.

## Data safety

- **`sensor.db` is never shipped or overwritten** — historical sensor data persists across
  every deploy. The schema uses `CREATE TABLE IF NOT EXISTS`, so new code that adds tables
  (e.g. the `meta` table) is additive on top of an existing database.
- **`config.toml` is provisioned only if absent** on the target — your bindkey/settings there
  are never clobbered by a push.
- Only an explicit, intentional schema migration would ever change existing rows.

## Autostart & runtime

- **Scheduled task `sensor_reader`** runs `…\.venv\Scripts\pythonw.exe -m lywsd03mmc_monitor`
  (no console window) with a logon trigger and auto-restart on crash.
- **For true start-at-boot with no one logged in, enable Windows auto-login for `Home`.**
  BLE (WinRT) needs an interactive desktop session, so the task triggers at logon, not in the
  session-0 service context. With auto-login, logon happens at boot and the app comes up.
- **Logs:** `C:\Users\<user>\sensor_reader\app.log` (rotating, 1 MB × 3). The app logs only to
  this file — it never writes to the console (required for `pythonw`).
- **Firewall:** push opens inbound TCP 8787 for LAN access (best effort; needs admin once).

## One-time prerequisites (already set up)

- Python 3.12 at `C:\Users\<user>\Python312` (python.org silent install — winget's source was broken).
- Passwordless SSH key auth for `<user>@<host>`.
- A Bluetooth LE radio (Intel Wireless Bluetooth).

## Managing the service on the PC

```bash
ssh <user>@<host> "schtasks /End /TN sensor_reader"    # stop
ssh <user>@<host> "schtasks /Run /TN sensor_reader"    # start
ssh <user>@<host> "powershell Get-Content C:\Users\<user>\sensor_reader\app.log -Tail 30"
```
