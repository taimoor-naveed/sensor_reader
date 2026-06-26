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
    from fastapi import Body, HTTPException

    @app.get("/api/gaps")
    def gaps(range: str = "week"):
        span = _RANGES.get(range, 604800)
        now = int(now_fn())
        return {"gaps": store.fillable_gaps(now - span, now)}

    @app.post("/api/backfill")
    async def do_backfill(payload: dict = Body(...)):
        if backfill_fn is None:
            raise HTTPException(status_code=503, detail="backfill unavailable")
        result = backfill_fn(int(payload["from"]), int(payload["to"]))
        if hasattr(result, "__await__"):
            result = await result
        return {"filled": int(result)}

    app.state.store = store
    app.state.live = state
    app.state.now_fn = now_fn
    app.state.backfill_fn = backfill_fn
    return app
