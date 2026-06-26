from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

_STATIC = Path(__file__).parent / "static"
_RANGES = {"hour": 3600, "day": 86400, "week": 604800}


def create_app(store, state, now_fn, backfill_fn=None) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/current")
    def current():
        return JSONResponse(state.snapshot(now_fn()))

    @app.get("/api/history")
    def history(range: str = "day"):
        span = _RANGES.get(range, 86400)
        now = int(now_fn())
        start, end = now - span, now
        if range == "week":
            points = []
            bands = store.hourly_rollup(start, end) + store.history_between(start, end)
            bands.sort(key=lambda b: b["hour_ts"])
        else:
            points = store.readings_between(start, end)
            bands = store.history_between(start, end)
        return {"range": range, "points": points, "bands": bands}

    # Phase 2 endpoints registered here by Tasks 13-14 (gaps, backfill).
    app.state.store = store
    app.state.live = state
    app.state.now_fn = now_fn
    app.state.backfill_fn = backfill_fn
    return app
