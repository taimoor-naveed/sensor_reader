# Deploying to an Ubuntu PC

> Full process, reboot behavior, and troubleshooting: **[`docs/deployment.md`](../docs/deployment.md)**.
> This file is the quick reference that lives next to the scripts.

The app runs on the Ubuntu machine as a **systemd user service** that autostarts at boot
(no login needed) and serves the dashboard on the LAN at `http://<host>:8787`.

**Target machine details live in `deploy/local.env`** (gitignored — copy
[`local.env.example`](local.env.example) and fill in your host). Site-specific notes:
`docs/deployment.local.md` (also gitignored).

## The three scripts

```bash
deploy/bootstrap.sh     # once per machine: apt deps, bluetooth group, linger, ufw
deploy/push.sh          # ship code + (re)start the service — run this for every update
deploy/restore-db.sh    # merge a backed-up sensor.db into the target's database
```

## Push code / updates

```bash
deploy/push.sh
# or target a different host/user for one run:
REMOTE=user@host deploy/push.sh
```

Each run (first run bootstraps the app dir, later runs are incremental):

1. Packs the `lywsd03mmc_monitor` package + `remote-update.sh` with `tar`, ships it over `scp`.
2. Extracts the new code into `~/sensor_reader` on the target.
3. Ensures the **virtual environment** (`.venv`) and installs `requirements-runtime.txt`.
4. Writes/refreshes the `sensor-reader` systemd **user** unit and restarts it.
5. Waits until `GET /api/current` responds, then reports success (and warns if the BLE
   scanner is failing even though the dashboard is up).

No `git` is needed on the target — transfer is `tar` + `scp` over SSH.

## Data safety

- **`sensor.db` is never shipped or overwritten** by `push.sh` — historical sensor data
  persists across every deploy. The schema uses `CREATE TABLE IF NOT EXISTS`, so new code
  that adds tables is additive on top of an existing database.
- **`config.toml` is provisioned only if absent** on the target — your bindkey/settings
  there are never clobbered by a push.
- `restore-db.sh` **merges** by default (`INSERT OR IGNORE` on the timestamp primary keys):
  it never drops rows the target already has, and re-running it is a no-op. It also keeps a
  `sensor.db*.pre-restore-<stamp>` snapshot on the target. `--replace` swaps the file instead.

## Autostart & runtime

- **systemd user unit `sensor-reader`** runs `~/sensor_reader/.venv/bin/python -m lywsd03mmc_monitor`
  with `Restart=always`.
- **Lingering** (`loginctl enable-linger <user>`, set by `bootstrap.sh`) is what makes it
  start at boot with nobody logged in and survive SSH disconnects. Unlike the old Windows
  setup, **no auto-login is needed** — BlueZ does not require an interactive session.
- **Logs:** `~/sensor_reader/app.log` (rotating, 1 MB × 3). The app logs only to this file;
  `journalctl --user -u sensor-reader` shows systemd-level events and hard crashes.
- **Firewall:** `bootstrap.sh` opens TCP 8787 if `ufw` is active.

## One-time prerequisites

- Passwordless SSH key auth to the target (`openssh-server` on the Ubuntu side).
- `deploy/bootstrap.sh` handles the rest: `python3-venv`, `python3-pip`, `bluez`, `curl`,
  `avahi-daemon`, the `bluetooth` group, lingering, and the firewall rule.
- A Bluetooth LE radio.

## Managing the service on the PC

```bash
ssh <user>@<host> "systemctl --user stop sensor-reader"
ssh <user>@<host> "systemctl --user start sensor-reader"
ssh <user>@<host> "systemctl --user status sensor-reader --no-pager"
ssh <user>@<host> "tail -n 40 ~/sensor_reader/app.log"
```
