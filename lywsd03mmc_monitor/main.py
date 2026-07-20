import asyncio
import logging
import time

import uvicorn

from lywsd03mmc_monitor.config import load_config
from lywsd03mmc_monitor.scanner import Scanner
from lywsd03mmc_monitor.state import LiveState
from lywsd03mmc_monitor.store import Store
from lywsd03mmc_monitor.web import create_app

LOG_FILE = "app.log"


def _log_config(log_file: str = LOG_FILE) -> dict:
    """Route all logging to a rotating file so the app never touches the console.
    Required for the deployed Windows service, which runs under pythonw.exe (no
    console): uvicorn's default stdout handler would otherwise crash at startup."""
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"file": {"format": fmt}},
        "handlers": {
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": log_file,
                "maxBytes": 1_000_000,
                "backupCount": 3,
                "encoding": "utf-8",
                "formatter": "file",
            }
        },
        "root": {"handlers": ["file"], "level": "INFO"},
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
    }


async def run(config_path: str = "config.toml") -> None:
    cfg = load_config(config_path)
    store = Store(cfg.db_path)
    state = LiveState(cfg.offline_after_seconds, cfg.battery_warn_below)
    scanner = Scanner(cfg.bindkey, store, state, now_fn=time.time, address=cfg.address)

    def backfill_fn(start_ts: int, end_ts: int, progress_cb=None):
        from lywsd03mmc_monitor.gatt import backfill
        return backfill(scanner.last_device, store, start_ts, end_ts, progress_cb=progress_cb)

    def update_fn():
        from lywsd03mmc_monitor.gatt import read_live
        return read_live(scanner.last_device)

    app = create_app(store, state, now_fn=time.time,
                     backfill_fn=backfill_fn, update_fn=update_fn)

    async def start_scanner():
        # A BLE failure (adapter off, permissions) must not take the dashboard
        # down with it — keep serving stored data and retry in the background.
        log = logging.getLogger("lywsd03mmc.scanner")
        while True:
            try:
                await scanner.start()
                return
            except Exception:
                log.exception("BLE scanner failed to start; retrying in 60s")
                await asyncio.sleep(60)

    server = uvicorn.Server(uvicorn.Config(
        app, host=cfg.host, port=cfg.port, log_level="info", log_config=_log_config()))
    scanner_task = asyncio.create_task(start_scanner())
    try:
        await server.serve()
    finally:
        scanner_task.cancel()
        await scanner.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
