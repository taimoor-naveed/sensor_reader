# Deployment Guide — Windows PC

How the LYWSD03MMC monitor is deployed to a Windows machine, how to push updates, and how it
behaves across reboots. The deploy scripts live in [`deploy/`](../deploy).

> **Machine-specific values** (real hostname, IP, user, paths) are deliberately not in this
> document. They live in two gitignored files: `deploy/local.env` (consumed by the scripts;
> copy [`deploy/local.env.example`](../deploy/local.env.example)) and
> `docs/deployment.local.md` (site notes). Placeholders like `<user>@<host>` below refer
> to those values.

---

## 1. What runs where

| Piece | Location |
|-------|----------|
| Target machine | `<user>@<host>` — Windows 10/11 with OpenSSH server |
| App directory | `C:\Users\<user>\sensor_reader` |
| Python | per-user install (see `WIN_PY` in `deploy/local.env`) |
| Virtual env | `C:\Users\<user>\sensor_reader\.venv` |
| Database | `C:\Users\<user>\sensor_reader\sensor.db` (persists across deploys) |
| Config | `C:\Users\<user>\sensor_reader\config.toml` (bindkey, host/port) |
| Logs | `C:\Users\<user>\sensor_reader\app.log` (rotating, 1 MB × 3) |
| Autostart | Scheduled task `sensor_reader`, trigger **At logon** |
| Dashboard | `http://<host>:8787` (bound to `0.0.0.0`, reachable on the LAN) |

The app runs under **`pythonw.exe`** (no console window), launched by the scheduled task.

---

## 2. Pushing code / updates

From the repo root:

```bash
deploy/push.sh                   # target comes from deploy/local.env
REMOTE=user@host deploy/push.sh  # override host/user for one run
```

What each run does (first run bootstraps, later runs are incremental — same script):

1. **Package** the `lywsd03mmc_monitor` package + deploy helpers into a `tar` archive.
2. **Upload** it with `scp` and extract into the app directory (over the old code).
3. **Provision `config.toml`** only if it is missing on the target (never overwrites).
4. **Ensure the venv** and `pip install -r deploy/requirements-runtime.txt`.
5. **(Re)register** the `sensor_reader` scheduled task.
6. **Restart** the app and **wait until `GET /api/current` responds** before reporting success.

No `git` is needed on the Windows side — transfer is `tar` + `scp` over SSH.

---

## 3. Reboot / autostart behavior

**Yes — the app starts automatically after a reboot,** provided auto-login is enabled.

- With **auto-login enabled** (`AutoAdminLogon=1`, `DefaultUserName=<user>` under
  `HKLM\…\Winlogon`), the PC logs in the app user at boot with no prompt.
- The `sensor_reader` task is triggered **At logon**, so it launches the app in the
  interactive desktop session immediately after that automatic login.

**Why "At logon" and not "At startup"?** BLE on Windows (WinRT) needs an interactive desktop
session. A task running in the session-0 service context (the "At startup" / "run whether
logged on or not" mode) cannot reliably drive the Bluetooth radio. Auto-login + a logon-triggered
task is the supported way to get unattended start **and** working BLE.

> If auto-login is ever turned off, the app will only start once someone logs in as the app
> user. Re-enable with (admin PowerShell on the PC):
> ```
> Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' AutoAdminLogon 1
> Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' DefaultUserName <user>
> Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' DefaultPassword '<password>'
> ```

The task is also set to **restart on crash** (3 attempts, 1 min apart) and has no execution
time limit, so transient failures self-heal.

---

## 4. Data safety

- **`sensor.db` is never shipped or overwritten** by a deploy — all historical readings/history
  persist. The schema uses `CREATE TABLE IF NOT EXISTS`, so deploying code that adds a table
  (e.g. `meta`) is additive on top of the existing database.
- **`config.toml` is only created if absent** — the bindkey and host/port on the target are
  never clobbered.
- Only a deliberate, explicit migration would ever modify existing rows.

---

## 5. Monitoring & managing the service

```bash
# Tail the log
ssh <user>@<host> "powershell Get-Content C:\Users\<user>\sensor_reader\app.log -Tail 40"

# Is it running?
ssh <user>@<host> "powershell \"Get-Process pythonw -EA SilentlyContinue | ? { \$_.Path -like '*sensor_reader*' } | Select Id,Path\""

# Stop / start / status
ssh <user>@<host> "schtasks /End /TN sensor_reader"
ssh <user>@<host> "schtasks /Run /TN sensor_reader"
ssh <user>@<host> "schtasks /Query /TN sensor_reader /V /FO LIST"

# Health check from anywhere on the LAN
curl http://<host>:8787/api/current
```

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| App not serving; `app.log` missing; task `LastTaskResult=1` | The app wrote to the console while running under `pythonw`. The app must log **only to file** — see `_log_config()` in `main.py`. Do not add console/stdout logging on the `run()` path. |
| Not reachable from another device | Windows Firewall — deploy opens inbound TCP 8787 best-effort (needs admin once): `netsh advfirewall firewall add rule name="sensor_reader 8787" dir=in action=allow protocol=TCP localport=8787`. |
| GATT Update/Fill slow (~20 s) | Normal on the Windows BLE stack (vs ~5 s on macOS). The connect latency is higher; it still completes. |
| Signal shows 0 bars briefly | Windows occasionally reports advert RSSI `-127` ("unknown"); the next advert corrects it. Cosmetic. |
| `winget` errors installing Python | Its source index can be broken — use the python.org silent installer instead (see §7). |

---

## 7. Reproducing the one-time bootstrap (fresh machine)

These are done once and are not part of `push.sh`:

1. **OpenSSH server** + passwordless **key auth** for the deploy user.
2. **Python 3.12** (per-user, no admin):
   ```cmd
   curl -L -f -o "%TEMP%\py312.exe" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
   "%TEMP%\py312.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=0 TargetDir=C:\Users\<user>\Python312
   ```
   (If `winget` works on the target, `winget install -e --id Python.Python.3.12 --scope user` is fine too.)
3. A working **Bluetooth LE** radio.
4. **Auto-login** enabled (see §3) for unattended start at boot.
5. **`deploy/local.env`** filled in from the example.

Then `deploy/push.sh` handles everything else (dir, venv, deps, task, firewall, launch).
