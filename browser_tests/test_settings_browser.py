"""Runtime preferences through the real APIs, isolated account sessions and browser storage."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect


def open_settings(page):
    page.get_by_role("button", name="Open menu", exact=True).click()
    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.get_by_role("dialog", name="Settings", exact=True)).to_be_visible()


def apply_counts(page, transcripts, stations):
    page.get_by_role("tab", name="Dashboard", exact=True).click()
    page.get_by_label("Transcript display count", exact=True).fill(str(transcripts))
    page.get_by_label("Last Heard station display count", exact=True).fill(str(stations))
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#settings-feedback")).to_have_text("Preferences applied.")
    expect(page.locator("#recordings .recording")).to_have_count(transcripts)
    expect(page.locator(".callsign-card")).to_have_count(stations)


def storage_key(page):
    return "repeater-scribe:preferences:v1:" + page.locator("body").get_attribute("data-preference-scope")


def test_apply_limits_refresh_search_reload_defaults_and_layout(page, application):
    page.goto(application[0] + "/")
    expect(page.locator(".callsign-card")).to_have_count(25)
    expect(page.locator("#recordings .recording")).not_to_have_count(0)
    total = page.locator("#total-count").text_content()
    available = len(page.request.get(application[0] + "/api/v1/recordings?limit=500").json()["items"])
    layout = page.evaluate("localStorage.getItem('dashboard-dock-state')")
    open_settings(page)
    apply_counts(page, 2, 2)
    expect(page.locator("#transcript-results")).to_contain_text("Showing 2 of")
    expect(page.locator("#callsigns-count")).to_have_text("2 shown")
    expect(page.locator("#total-count")).to_have_text(total)
    # Increasing beyond the former Last Heard default exercises the real service.
    apply_counts(page, 3, 40)
    apply_counts(page, 1, 3)
    assert page.evaluate("localStorage.getItem('dashboard-dock-state')") == layout
    page.get_by_role("button", name="Cancel", exact=True).click()
    page.evaluate("async () => { await loadJobs(); await loadCallsigns(); }")
    expect(page.locator("#recordings .recording")).to_have_count(1)
    expect(page.locator(".callsign-card")).to_have_count(3)
    page.evaluate("undockPanel('callsigns')")
    page.locator("#refresh-callsigns").click()
    expect(page.locator(".callsign-card")).to_have_count(3)
    page.evaluate("activatePanel('transcripts')")
    page.get_by_placeholder("Search transcripts").fill("radio check")
    expect(page.locator("#recordings .recording")).to_have_count(1)
    expect(page.locator("#recordings .recording").first).to_contain_text("radio check")
    page.reload()
    expect(page.locator("#recordings .recording")).to_have_count(1)
    expect(page.locator(".callsign-card")).to_have_count(3)
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("1")
    page.get_by_role("button", name="Restore defaults").click()
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("500")
    expect(page.get_by_label("Last Heard station display count", exact=True)).to_have_value("25")
    expect(page.locator("#recordings .recording")).to_have_count(1)
    page.get_by_role("button", name="Cancel", exact=True).click()
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("1")
    page.get_by_role("button", name="Restore defaults").click()
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#recordings .recording")).to_have_count(available)
    expect(page.locator(".callsign-card")).to_have_count(25)


@pytest.mark.parametrize("width", [1440, 375])
def test_tabs_keyboard_cancel_escape_and_responsive_layout(page, application, width):
    page.set_viewport_size({"width": width, "height": 850})
    page.goto(application[0] + "/")
    open_settings(page)
    dashboard = page.get_by_role("tab", name="Dashboard", exact=True)
    appearance = page.get_by_role("tab", name="Appearance", exact=True)
    expect(dashboard).to_be_focused()
    dashboard.press("ArrowDown")
    expect(appearance).to_be_focused()
    expect(appearance).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("tabpanel", name="Appearance")).to_contain_text("Dark operator")
    expect(page.get_by_role("combobox")).to_have_count(0)
    appearance.press("Home")
    page.get_by_label("Transcript display count", exact=True).fill("7")
    page.get_by_role("button", name="Close settings", exact=True).click()
    expect(page.get_by_role("button", name="Open menu", exact=True)).to_be_focused()
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("500")
    page.get_by_label("Transcript display count", exact=True).fill("9")
    page.keyboard.press("Escape")
    expect(page.locator("#settings-modal")).not_to_be_visible()
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("500")
    # Native modal keeps keyboard focus in the dialog, including wraparound.
    page.get_by_role("button", name="Apply", exact=True).focus()
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Close settings", exact=True)).to_be_focused()
    tab_bounds = dashboard.bounding_box()
    panel_bounds = page.get_by_role("tabpanel", name="Dashboard").bounding_box()
    assert tab_bounds["x"] + tab_bounds["width"] < panel_bounds["x"]
    assert page.locator(".settings-content").evaluate("el => el.scrollWidth <= el.clientWidth")
    if directory := os.environ.get("ASLT_SETTINGS_SCREENSHOTS"):
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(directory) / f"settings-{width}.png"))


@pytest.mark.parametrize("value", ["", "0", "-1", "1.5", "501", "99999999"])
def test_invalid_input_does_not_save_or_request(page, application, value):
    page.goto(application[0] + "/")
    open_settings(page)
    before = page.evaluate("key => localStorage.getItem(key)", storage_key(page))
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    field = page.get_by_label("Transcript display count", exact=True)
    field.fill(value)
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(field).to_have_attribute("aria-invalid", "true")
    expect(field).to_be_focused()
    expect(page.locator("#settings-feedback")).to_contain_text("whole number from 1 to 500")
    assert page.evaluate("key => localStorage.getItem(key)", storage_key(page)) == before
    assert not any("recordings?" in url or "last-heard?" in url for url in requests)
    field.fill("10")
    page.get_by_label("Last Heard station display count", exact=True).fill("101")
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#settings-feedback")).to_contain_text("whole number from 1 to 100")


@pytest.mark.parametrize("saved", ["{broken", "null", "[]", json.dumps({
    "dashboard.transcriptLimit": 0, "dashboard.lastHeardLimit": 100000, "appearance.theme": "obsolete"
}), json.dumps({"dashboard.transcriptLimit": "10", "dashboard.lastHeardLimit": 1.5})])
def test_invalid_storage_defaults_and_identity_isolation(page, application, saved):
    page.goto(application[0] + "/")
    key = storage_key(page)
    page.evaluate("([key, value]) => localStorage.setItem(key, value)", [key, saved])
    page.reload()
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("500")
    expect(page.get_by_label("Last Heard station display count", exact=True)).to_have_value("25")
    assert page.evaluate("RuntimeSettings.get('appearance.theme')") == "operator-dark"
    apply_counts(page, 2, 2)
    page.context.add_cookies([{"name": "__Host-aslt_session", "value": "browser-viewer",
                              "url": application[0], "secure": True, "httpOnly": True}])
    page.reload()
    assert storage_key(page) != key
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("500")
    page.context.add_cookies([{"name": "__Host-aslt_session", "value": "browser-operator",
                              "url": application[0], "secure": True, "httpOnly": True}])
    page.reload()
    expect(page.locator("#recordings .recording")).to_have_count(2)


def test_extension_pages_and_personal_action_boundary(page, application):
    page.goto(application[0] + "/")
    page.evaluate("""() => {
        RuntimeSettings.registerPage({id: 'users', label: 'Users', available: () => false});
        RuntimeSettings.registerPage({id: 'access', label: 'API Access', mount(panel) {
            const button = document.createElement('button');
            button.textContent = 'Page-owned action'; panel.append(button);
        }});
    }""")
    open_settings(page)
    expect(page.get_by_role("tab", name="Users", exact=True)).to_have_count(0)
    page.get_by_role("tab", name="API Access", exact=True).click()
    expect(page.get_by_role("button", name="Page-owned action")).to_be_visible()
    expect(page.get_by_role("button", name="Apply", exact=True)).to_have_count(0)
    page.get_by_role("tab", name="Dashboard", exact=True).click()
    expect(page.get_by_role("button", name="Apply", exact=True)).to_be_visible()


def test_apply_preserves_playback_and_expanded_evidence(page, application):
    page.goto(application[0] + "/")
    page.evaluate("activatePanel('transcripts')")
    play = page.locator('.recording[data-source-path="sample.wav"] [data-recording-key]')
    play.click()
    page.wait_for_function("() => !player.paused && player.currentTime > 0")
    page.evaluate("""() => {
        window.mediaEvents = [];
        for (const type of ['loadstart', 'play', 'pause']) player.addEventListener(type, () => mediaEvents.push(type));
        document.querySelector('.confidence-evidence').open = true;
    }""")
    evidence_call = page.locator('.confidence-evidence[open]').locator('xpath=ancestor::article').get_attribute('data-callsign')
    before = page.evaluate("({time: player.currentTime, src: player.src})")
    open_settings(page)
    apply_counts(page, 2, 40)
    assert page.evaluate("player.src") == before["src"]
    assert page.evaluate("player.currentTime") >= before["time"]
    assert page.evaluate("mediaEvents") == []
    expect(page.locator(f'.callsign-card[data-callsign="{evidence_call}"] details')).to_have_attribute("open", "")


def test_periodic_refresh_uses_applied_limits(page, application):
    page.clock.install()
    page.goto(application[0] + "/")
    open_settings(page)
    apply_counts(page, 2, 2)
    page.get_by_role("button", name="Cancel", exact=True).click()
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.clock.fast_forward(61000)
    page.wait_for_function("() => document.querySelectorAll('.callsign-card').length === 2")
    assert any("recordings?limit=2" in url for url in requests)
    assert any("last-heard?limit=2" in url for url in requests)
    expect(page.locator("#recordings .recording")).to_have_count(2)
    expect(page.locator(".callsign-card")).to_have_count(2)


@pytest.mark.parametrize("endpoint,loader,field,cards", [
    ("recordings", "loadJobs", "Transcript display count", "#recordings .recording"),
    ("callsigns/last-heard", "loadCallsigns", "Last Heard station display count", ".callsign-card"),
])
def test_slow_previous_refresh_cannot_replace_new_limit(page, application, endpoint, loader, field, cards):
    page.goto(application[0] + "/")
    open_settings(page)
    apply_counts(page, 3, 3)
    old_data = page.request.get(application[0] + f"/api/v1/{endpoint}?limit=3").json()
    pending = []
    page.route(f"**/api/v1/{endpoint}?limit=3", lambda route: pending.append(route))
    with page.expect_request(f"**/api/v1/{endpoint}?limit=3"):
        page.evaluate(f"void {loader}()")
    page.get_by_label(field, exact=True).fill("2")
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator(cards)).to_have_count(2)
    pending[0].fulfill(json=old_data)
    page.evaluate("async () => { await new Promise(resolve => requestAnimationFrame(resolve)); }")
    expect(page.locator(cards)).to_have_count(2)


def test_storage_write_failure_preserves_applied_preferences(page, application):
    page.goto(application[0] + "/")
    open_settings(page)
    apply_counts(page, 3, 3)
    page.evaluate("""() => {
        const setItem = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
            if (key.startsWith('repeater-scribe:preferences:')) throw new DOMException('Storage blocked', 'QuotaExceededError');
            return setItem.call(this, key, value);
        };
    }""")
    page.get_by_label("Transcript display count", exact=True).fill("2")
    page.get_by_role("button", name="Apply", exact=True).click()
    expect(page.locator("#settings-feedback")).to_contain_text("Allow browser storage")
    expect(page.locator("#recordings .recording")).to_have_count(3)
    page.get_by_role("button", name="Cancel", exact=True).click()
    open_settings(page)
    expect(page.get_by_label("Transcript display count", exact=True)).to_have_value("3")
