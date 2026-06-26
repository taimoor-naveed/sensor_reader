import asyncio
import time

import uvicorn

from lywsd03mmc_monitor.config import load_config
from lywsd03mmc_monitor.scanner import Scanner
from lywsd03mmc_monitor.state import LiveState
from lywsd03mmc_monitor.store import Store
from lywsd03mmc_monitor.web import create_app


async def run(config_path: str = "config.toml") -> None:
    cfg = load_config(config_path)
    store = Store(cfg.db_path)
    state = LiveState(cfg.offline_after_seconds, cfg.battery_warn_below)
    scanner = Scanner(cfg.bindkey, store, state, now_fn=time.time, address=cfg.address)

    def backfill_fn(start_ts: int, end_ts: int):
        from lywsd03mmc_monitor.gatt import backfill  # Phase 2
        return backfill(scanner.last_device, store, start_ts, end_ts)

    app = create_app(store, state, now_fn=time.time, backfill_fn=backfill_fn)

    await scanner.start()
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="info"))
    try:
        await server.serve()
    finally:
        await scanner.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
