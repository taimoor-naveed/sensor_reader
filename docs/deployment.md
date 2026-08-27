# Deployment Guide — Ubuntu PC

How the LYWSD03MMC monitor is deployed to an Ubuntu machine, how to push updates, how to
restore backed-up sensor history, and how it behaves across reboots. The deploy scripts live
in [`deploy/`](../deploy).

> **Machine-specific values** (real hostname, IP, user, paths) are deliberately not in this
> document. They live in two gitignored files: `deploy/local.env` (consumed by the scripts;
> copy [`deploy/local.env.example`](../deploy/local.env.example)) and
> `docs/deployment.local.md` (site notes). Placeholders like `<user>@<host>` below refer
> to those values.

---

## 1. What runs where

| Piece | Location |
|-------|----------|
| Target machine | `<user>@<host>` — Ubuntu with `openssh-server` |
| App directory | `~/sensor_reader` (`APP_DIR` in `local.env`) |
| Python | the distro `python3` (3.11+ required for `tomllib`) |
| Virtual env | `~/sensor_reader/.venv` |
| Database | `~/sensor_reader/sensor.db` (persists across deploys) |
| Config | `~/sensor_reader/config.toml` (bindkey, host/port) |
| Logs | `~/sensor_reader/app.log` (rotating, 1 MB × 3) |
| Autostart | systemd **user** unit `sensor-reader` + lingering |
| Dashboard | `http://<host>:8787` (bound to `0.0.0.0`, reachable on the LAN) |

Nothing in the app is OS-specific: `bleak` talks to **BlueZ over D-Bus** on Linux instead of
WinRT, and the scanner uses ordinary active scanning, so no BlueZ experimental flags are
needed. All the platform difference lives in `deploy/`.

---

## 2. One-time host prep

```bash
deploy/bootstrap.sh              # from the Mac; runs deploy/bootstrap-remote.sh over sudo
```

It is idempotent and does:

1. `apt install python3-venv python3-pip bluez curl avahi-daemon`.
2. Enables the `bluetooth` service and adds the app user to the **`bluetooth` group**
   (D-Bus access to `org.bluez`).
3. **`loginctl enable-linger <user>`** — the systemd user manager then starts at boot and
   keeps running with nobody logged in.
4. `ufw allow 8787/tcp` if `ufw` is active.
5. Prints a check of `python3`, lingering, `/run/user/<uid>`, and adapter access.

Prerequisites it does *not* do: install `openssh-server` and authorize your SSH key.

If the `bluetooth` group had to be added, **reboot** — the group only reaches the service
when the user manager restarts, and the reboot doubles as a test of unattended autostart.

---

## 3. Pushing code / updates

From the repo root:

```bash
deploy/push.sh                   # target comes from deploy/local.env
REMOTE=user@host deploy/push.sh  # override host/user for one run
```

What each run does (first run bootstraps the app dir, later runs are incremental — same script):

1. **Package** the `lywsd03mmc_monitor` package + `remote-update.sh` into a `tar` archive.
2. **Upload** with `scp` and extract into the app directory (over the old code).
3. **Provision `config.toml`** only if it is missing on the target (never overwrites).
4. **Ensure the venv** and `pip install -r deploy/requirements-runtime.txt`.
5. **Write the systemd user unit** `sensor-reader` and `systemctl --user enable --now` it.
6. **Restart** and **wait until `GET /api/current` responds** before reporting success. It
   also greps `app.log` for `BLE scanner failed to start` and warns — the dashboard serves
   stored data fine with a dead radio, which is otherwise easy to miss.

---

## 4. Restoring backed-up sensor history

`backups/<host>-<date>/sensor.db` holds a live-SQLite `.backup` snapshot pulled off the old
machine. To put it on the target:

```bash
deploy/restore-db.sh                        # newest backups/*/sensor.db, MERGE mode
deploy/restore-db.sh path/to/sensor.db      # explicit source
deploy/restore-db.sh --replace              # overwrite the target DB instead of merging
```

Sequence: verify the backup locally (`PRAGMA integrity_check` + row counts) → stop the
service → snapshot the target's current `sensor.db*` as `sensor.db*.pre-restore-<stamp>` →
upload → merge (or replace) → start the service → print the resulting data extent via
`/api/history?range=all`.

**Merge mode is the default and is safe to re-run.** `readings.ts` and `history.hour_ts` are
primary keys, so `INSERT OR IGNORE` adds only rows the target lacks and never rewrites rows
it already has. That means you can deploy first, let the app collect for a while, and restore
afterwards without losing either side.

With `--replace`, the stale `sensor.db-wal` / `sensor.db-shm` are deleted along with the old
database — SQLite must never find a WAL belonging to a different database file.

> **Gap in the data:** the backup ends at the moment the snapshot was taken on the old
> machine. Everything between that and the first reading on Ubuntu is simply missing — the
> sensor only keeps ~2 days of hourly history since its last boot, so a `Fill` from the
> dashboard can recover at most that much of the tail.

---

## 5. Reboot / autostart behavior

**Yes — the app starts automatically after a reboot, with nobody logged in.**

- The unit is a systemd **user** unit (`~/.config/systemd/user/sensor-reader.service`),
  enabled into `default.target`.
- **`loginctl enable-linger <user>`** makes systemd start that user's manager at boot and
  keep it alive with no session — this is also what stops the app from being killed when an
  SSH session ends.
- `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=0`: crashes self-heal
  indefinitely, and BLE failures are additionally retried inside the app every 60 s.

**No auto-login is required** — that was a Windows/WinRT constraint (BLE needed an
interactive desktop session). BlueZ has no such restriction, so this setup is strictly
simpler than the old one.

Ordering against `bluetooth.target` is not expressible from a user unit (it is a system
unit); the app's own retry loop covers a radio that isn't ready yet at boot.

---

## 6. Data safety

- **`sensor.db` is never shipped or overwritten** by a deploy — all historical readings and
  history persist. The schema uses `CREATE TABLE IF NOT EXISTS`, so deploying code that adds
  a table is additive on top of the existing database.
- **`config.toml` is only created if absent** — the bindkey and host/port on the target are
  never clobbered.
- Only a deliberate, explicit migration or `restore-db.sh --replace` changes existing rows.

---

## 7. Monitoring & managing the service

```bash
# Tail the app log
ssh <user>@<host> "tail -n 40 ~/sensor_reader/app.log"

# Service state / systemd-level events
ssh <user>@<host> "systemctl --user status sensor-reader --no-pager"
ssh <user>@<host> "journalctl --user -u sensor-reader -n 50 --no-pager"

# Stop / start / restart
ssh <user>@<host> "systemctl --user stop sensor-reader"
ssh <user>@<host> "systemctl --user start sensor-reader"

# Bluetooth adapter
ssh <user>@<host> "systemctl status bluetooth --no-pager; bluetoothctl show"

# Health check from anywhere on the LAN
curl http://<host>:8787/api/current
```

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `app.log` shows `BLE scanner failed to start` | BlueZ/D-Bus access. Check `bluetoothctl show` as the app user (expect `Powered: yes`), confirm the user is in the `bluetooth` group (`id -nG`), and reboot if the group was just added — the running user manager keeps its old group set. |
| App dies when the SSH session ends, or doesn't come back after a reboot | Lingering is off: `sudo loginctl enable-linger <user>`. `push.sh`/`remote-update.sh` warn about this. |
| `systemctl --user` fails over SSH with "Failed to connect to bus" | `XDG_RUNTIME_DIR` missing in a non-interactive SSH command; the scripts export `/run/user/$(id -u)` themselves. If `/run/user/<uid>` doesn't exist at all, enable lingering (above). |
| `python3 -m venv` fails | `sudo apt install -y python3-venv`. |
| `pip install` fails building `dbus-fast` (no wheel for a very new Python) | `sudo apt install -y build-essential python3-dev`, or use an older interpreter via `REMOTE_PY`. |
| Not reachable from another device | `sudo ufw allow 8787/tcp` (bootstrap does this when ufw is active), and check `host = "0.0.0.0"` in `config.toml`. |
| `<host>` doesn't resolve from the phone/Mac | Ubuntu registers its hostname with the router's DHCP/DNS like Windows did; `avahi-daemon` (installed by bootstrap) also gives `<host>.local`. The LAN IP always works. |
| GATT `Update`/`Fill` timing | BlueZ connect latency sits between macOS (~5 s) and the old Windows stack (~20 s). |

---

## 9. Notes on the sensor address

On Linux, `device.address` is the real MAC rather than the CoreBluetooth UUID macOS reports
(this sensor's MAC is noted in the local `config.toml`). `config.toml` deliberately leaves
`address = ""` so the
same file works on both: with a single sensor the scanner matches by service UUID + product
id + successful decrypt. Pin the MAC only if another encrypted Xiaomi sensor ever shares the
air — and keep it out of the Mac's copy of the config.
