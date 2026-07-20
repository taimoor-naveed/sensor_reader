from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from harness import build_app, LiveServer, NOW

SHOTS = Path(__file__).parent / "screenshots"
SHOTS.mkdir(exist_ok=True)

APP_READY = "window.__app && __app.state.data && __app.state.data.series.length > 0"


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield p, browser
        browser.close()


def _desktop_page(browser):
    return browser.new_page(viewport={"width": 1280, "height": 900})


def _mobile_ctx(p, browser):
    return browser.new_context(**p.devices["iPhone 13"])


def _nonnull_temp_buckets(page):
    return page.evaluate(
        "() => __app.state.data.series.filter(p => p.temp != null).length")


def test_desktop_renders_and_screenshot(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#temp")
        assert page.inner_text("#temp") not in ("--", "")
        page.wait_for_function(APP_READY)
        page.screenshot(path=str(SHOTS / "desktop.png"), full_page=True)
        page.close()


def test_mobile_renders_no_horizontal_overflow(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        ctx = _mobile_ctx(p, browser)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#temp")
        page.wait_for_function(APP_READY)
        no_overflow = page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert no_overflow, "dashboard overflows horizontally on mobile"
        page.screenshot(path=str(SHOTS / "mobile.png"), full_page=True)
        ctx.close()


def test_both_charts_have_data(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(APP_READY)
        counts = page.evaluate("""() => {
            const nn = c => c.getOption().series.find(s => s.id === 'avg')
                              .data.filter(d => d[1] != null).length;
            return [nn(__app.charts.t), nn(__app.charts.h)];
        }""")
        assert counts[0] > 0 and counts[1] > 0, f"chart series empty: {counts}"
        page.close()


def test_range_chips_switch_and_refetch(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(APP_READY)
        page.click('button[data-r="7d"]')
        page.wait_for_function("__app.state.token === '7d' && __app.state.data.bucket === 1800")
        assert "active" in page.get_attribute('button[data-r="7d"]', "class")
        page.click('button[data-r="6h"]')
        page.wait_for_function("__app.state.token === '6h' && __app.state.data.bucket === 120")
        assert "active" in page.get_attribute('button[data-r="6h"]', "class")
        page.close()


def test_all_range_complete_without_gestures(pw):
    # Regression for v2's worst bug: "All" (or any wide range) rendered with
    # missing data until a zoom/pan gesture happened to trigger a refetch.
    # v3 must have the complete extent loaded immediately after the click.
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(APP_READY)
        page.click('button[data-r="all"]')
        page.wait_for_function("__app.state.token === 'all' && __app.state.data.range === 'all'")
        data = page.evaluate("() => __app.state.data")
        assert data["from"] <= NOW - 167 * 3600, "All range does not reach the earliest data"
        assert data["to"] >= NOW
        nonnull = _nonnull_temp_buckets(page)
        assert nonnull > 100, f"only {nonnull} buckets have data on the All range"
        page.close()


def test_fresh_deploy_history_shows_in_24h(pw):
    # Fresh deploy: sparse recent live readings + ~3 days of hourly device
    # history. The 24h chart must span the day via the history fallback, not
    # just show a tiny recent cluster.
    p, browser = pw
    with LiveServer(build_app("fresh_deploy")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(APP_READY)
        span_h = page.evaluate("""() => {
            const pts = __app.state.data.series.filter(p => p.temp != null);
            return (pts[pts.length-1].t - pts[0].t) / 3600;
        }""")
        assert span_h >= 12, f"24h view only spans {span_h}h; history not merged"
        page.close()


def test_mobile_tap_scrub_shows_readout(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        ctx = _mobile_ctx(p, browser)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(APP_READY)
        page.locator("#tchart").scroll_into_view_if_needed()
        page.wait_for_timeout(200)
        box = page.locator("#tchart").bounding_box()
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_function(
            "document.getElementById('tcard').classList.contains('scrub')")
        assert "°" in page.inner_text("#tread")     # value readout
        assert "%" in page.inner_text("#hread")     # synced across both charts
        ctx.close()


def test_scrub_readout_reverts_to_summary(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(APP_READY)
        box = page.locator("#tchart").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_function(
            "document.getElementById('tcard').classList.contains('scrub')")
        # readout auto-expires a few seconds after the pointer leaves
        page.mouse.move(10, 10)
        page.evaluate("() => { __app.state.scrubUntil = Date.now() - 1; }")
        page.wait_for_function(
            "!document.getElementById('tcard').classList.contains('scrub')")
        assert "↓" in page.inner_text("#tsub")      # summary is back
        page.close()


def test_empty_scenario_no_js_errors(pw):
    p, browser = pw
    with LiveServer(build_app("empty")) as srv:
        errors = []
        page = _desktop_page(browser)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(srv.url, wait_until="networkidle")
        for r in ["6h", "24h", "7d", "30d", "all"]:
            page.click(f'button[data-r="{r}"]')
            page.wait_for_timeout(150)
        assert page.inner_text("#temp") == "--"
        assert errors == [], f"JS errors on empty data: {errors}"
        page.close()


def test_signal_bars_render(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function("document.querySelectorAll('#signalBars i').length === 4")
        page.close()


def test_update_button_cycles(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.click("#updateBtn")
        page.wait_for_function(
            "document.querySelector('#temp').textContent.includes('22.9')", timeout=5000)
        page.close()


def test_low_battery_badge_shows(pw):
    p, browser = pw
    with LiveServer(build_app("low_battery")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("text=Low battery")
        page.close()


def test_offline_state_shows(pw):
    p, browser = pw
    with LiveServer(build_app("offline")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function("document.querySelector('#liveBadge').textContent === 'Offline'")
        page.close()


def test_fetch_button_always_visible_and_neutral_without_gaps(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_timeout(800)               # let refreshGaps run
        assert page.is_visible("#gapWrap")        # fetch control is always available
        assert page.is_visible("#fillBtn")
        assert page.evaluate("()=>document.querySelector('#gapWrap').classList.contains('gap')") is False
        page.close()


def test_gap_fill_touch_flow(pw):
    p, browser = pw
    with LiveServer(build_app("with_gaps")) as srv:
        ctx = _mobile_ctx(p, browser)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#gapWrap", state="visible")
        assert "missing" in page.inner_text("#gapFillable")
        page.locator("#fillBtn").scroll_into_view_if_needed()
        page.tap("#fillBtn")                     # touch, not click
        page.wait_for_function(
            "document.querySelector('#fillProgressText').textContent.includes('Filled')",
            timeout=8000)
        ctx.close()


def test_backfill_clears_fillable_after_fill(pw):
    p, browser = pw
    with LiveServer(build_app("with_gaps")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#fillBtn")
        page.click("#fillBtn")
        page.wait_for_function(
            "document.querySelector('#fillProgressText').textContent.includes('Filled')",
            timeout=8000)
        page.wait_for_function(
            "!document.querySelector('#gapWrap').classList.contains('gap')", timeout=5000)
        page.close()


def test_backfill_failure_reenables_button(pw):
    p, browser = pw
    with LiveServer(build_app("with_gaps")) as srv:
        page = _desktop_page(browser)
        page.route("**/api/backfill",
                   lambda route: route.fulfill(status=503, content_type="application/json", body="{}"))
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#fillBtn")
        page.click("#fillBtn")
        page.wait_for_function(
            "document.querySelector('#fillBtn') && document.querySelector('#fillBtn').disabled === false",
            timeout=8000)
        assert "Fetch from sensor" in page.inner_text("#fillBtn")
        page.close()
