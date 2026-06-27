from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from harness import build_app, LiveServer

SHOTS = Path(__file__).parent / "screenshots"
SHOTS.mkdir(exist_ok=True)


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield p, browser
        browser.close()


def _desktop_page(browser):
    return browser.new_page(viewport={"width": 1280, "height": 900})


def test_desktop_renders_and_screenshot(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#temp")
        assert page.inner_text("#temp") not in ("--", "")
        page.screenshot(path=str(SHOTS / "desktop.png"), full_page=True)
        page.close()


def test_mobile_renders_no_horizontal_overflow(pw):
    p, browser = pw
    iphone = p.devices["iPhone 13"]            # mobile viewport + has_touch
    with LiveServer(build_app("normal")) as srv:
        ctx = browser.new_context(**iphone)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#temp")
        no_overflow = page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert no_overflow, "dashboard overflows horizontally on mobile"
        page.screenshot(path=str(SHOTS / "mobile.png"), full_page=True)
        ctx.close()


def test_range_toggle_moves_active_class(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        for r in ["6h", "24h", "7d", "all"]:
            page.click(f'button[data-r="{r}"]')
            assert "active" in page.get_attribute(f'button[data-r="{r}"]', "class")
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


def test_charts_pan_in_sync(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_function(
            "window.tempChart && window.humChart && window.tempChart.data.datasets[0].data.length>0")
        synced = page.evaluate("""() => {
            tempChart.options.scales.x.min = 1000000; tempChart.options.scales.x.max = 2000000;
            tempChart.options.plugins.zoom.zoom.onZoomComplete({chart: tempChart});
            return humChart.options.scales.x.min === 1000000 && humChart.options.scales.x.max === 2000000;
        }""")
        assert synced
        page.close()


def test_empty_scenario_no_js_errors(pw):
    p, browser = pw
    with LiveServer(build_app("empty")) as srv:
        errors = []
        page = _desktop_page(browser)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(srv.url, wait_until="networkidle")
        page.click('button[data-r="7d"]')
        page.wait_for_timeout(500)
        assert page.inner_text("#temp") == "--"
        assert errors == [], f"JS errors on empty data: {errors}"
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


def test_no_gap_banner_hidden_when_no_gaps(pw):
    p, browser = pw
    with LiveServer(build_app("normal")) as srv:
        page = _desktop_page(browser)
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_timeout(800)               # let refreshGaps run
        assert page.is_hidden("#gapWrap")
        page.close()


def test_gap_fill_touch_flow(pw):
    p, browser = pw
    iphone = p.devices["iPhone 13"]
    with LiveServer(build_app("with_gaps")) as srv:
        ctx = browser.new_context(**iphone)
        page = ctx.new_page()
        page.goto(srv.url, wait_until="networkidle")
        page.wait_for_selector("#gapWrap", state="visible")
        assert "fillable" in page.inner_text("#gapFillable")
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
        # all fillable gaps resolved -> banner clears
        page.wait_for_selector("#gapWrap", state="hidden", timeout=5000)
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
        assert page.inner_text("#fillBtn") == "Fill from sensor"
        page.close()
