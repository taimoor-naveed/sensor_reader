from harness import build_app, LiveServer
import httpx


def test_server_serves_current():
    with LiveServer(build_app("normal")) as srv:
        r = httpx.get(srv.url + "/api/current")
        assert r.status_code == 200
        assert r.json()["temperature"] == 21.4


def test_with_gaps_reports_gaps():
    with LiveServer(build_app("with_gaps")) as srv:
        r = httpx.get(srv.url + "/api/gaps?range=week")
        assert len(r.json()["fillable"]) >= 5
