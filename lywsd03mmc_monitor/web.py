import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lywsd03mmc_monitor.protocol import Reading

_STATIC = Path(__file__).parent / "static"
_RANGES = {"hour": 3600, "day": 86400, "week": 604800}


def create_app(store, state, now_fn, backfill_fn=None, update_fn=None) -> FastAPI:
    app = FastAPI()
    app.state.gatt_busy = False
    app.state.backfill_status = {"state": "idle", "received": 0, "total": 0,
                                 "filled": 0, "error": None}

    # Serve vendor JS (Chart.js etc.) and other static assets
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/current")
    def current():
        now = now_fn()
        snap = state.snapshot(now)
        day = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = int(day.timestamp())
        today = store.today_high_low(day_start, day_start + 86400)
        snap.update({
            "today_high": today["high"], "today_low": today["low"],
            "today_high_ts": today["high_ts"], "today_low_ts": today["low_ts"],
        })
        return JSONResponse(snap)

    @app.get("/api/history")
    def history(range: str = None,
                start: int = Query(None, alias="from"),
                end: int = Query(None, alias="to")):
        now = int(now_fn())
        if start is None or end is None:
            span = _RANGES.get(range or "day", 86400)
            start, end = now - span, now
        win = store.history_window(int(start), int(end))
        return {"range": range or "custom", "points": win["points"],
                "bands": win["bands"], "extent": store.extent()}

    @app.get("/api/gaps")
    def gaps(range: str = None,
             start: int = Query(None, alias="from"),
             end: int = Query(None, alias="to")):
        now = int(now_fn())
        if start is None or end is None:
            span = _RANGES.get(range or "week", 604800)
            start, end = now - span, now
        boot = store.get_device_boot_epoch()
        return store.classify_gaps(int(start), int(end), boot)

    @app.post("/api/update")
    async def do_update():
        if update_fn is None:
            raise HTTPException(status_code=503, detail="update unavailable")
        if app.state.gatt_busy:
            raise HTTPException(status_code=409, detail="sensor busy")
        app.state.gatt_busy = True
        try:
            result = update_fn()
            if hasattr(result, "__await__"):
                result = await result
            now = now_fn()
            state.update(
                Reading(temperature=result.get("temperature"),
                        humidity=result.get("humidity"),
                        battery=result.get("battery")),
                state.rssi, now, source="gatt")
            store.add_reading(int(now), result.get("temperature"),
                              result.get("humidity"), result.get("battery"), state.rssi)
            if result.get("boot_epoch") is not None:
                store.set_device_boot_epoch(int(result["boot_epoch"]))
            return JSONResponse(state.snapshot(now))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"update failed: {e}")
        finally:
            app.state.gatt_busy = False

    @app.post("/api/backfill")
    async def do_backfill(payload: dict = Body(...)):
        if backfill_fn is None:
            raise HTTPException(status_code=503, detail="backfill unavailable")
        if app.state.gatt_busy:
            raise HTTPException(status_code=409, detail="sensor busy")
        app.state.gatt_busy = True
        app.state.backfill_status = {"state": "connecting", "received": 0,
                                     "total": 0, "filled": 0, "error": None}
        start, end = int(payload["from"]), int(payload["to"])

        async def run():
            try:
                def progress(received, total):
                    app.state.backfill_status.update(
                        state="reading", received=received, total=total)
                result = backfill_fn(start, end, progress)
                if hasattr(result, "__await__"):
                    result = await result
                app.state.backfill_status.update(state="done", filled=int(result))
            except Exception as e:
                app.state.backfill_status.update(state="error", error=str(e))
            finally:
                app.state.gatt_busy = False

        asyncio.create_task(run())
        return {"started": True}

    @app.get("/api/backfill/status")
    def backfill_status():
        return app.state.backfill_status

    app.state.store = store
    app.state.live = state
    app.state.now_fn = now_fn
    app.state.backfill_fn = backfill_fn
    app.state.update_fn = update_fn
    return app
