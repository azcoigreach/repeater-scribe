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
        RuntimeSettings.registerPage({id: 'access', label: 'Automation', mount(panel) {
            const button = document.createElement('button');
            button.textContent = 'Page-owned action'; panel.append(button);
        }});
    }""")
    open_settings(page)
    expect(page.get_by_role("tab", name="Users", exact=True)).to_have_count(0)
    page.get_by_role("tab", name="Automation", exact=True).click()
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


def test_admin_users_provisions_changes_access_and_revokes_sessions(page, application):
    page.set_viewport_size({"width": 375, "height": 850})
    page.context.add_cookies([{
        "name": "__Host-aslt_session", "value": "browser-admin", "url": application[0],
        "secure": True, "httpOnly": True,
    }])
    page.goto(application[0] + "/")
    open_settings(page)
    users_tab = page.get_by_role("tab", name="Users", exact=True)
    expect(users_tab).to_be_visible()
    users_tab.click()
    users = page.locator(".account-table tbody tr")
    expect(users).to_have_count(5)
    assert page.locator(".settings-content").evaluate("el => el.scrollWidth <= el.clientWidth")
    assert page.locator(".account-table-wrap").evaluate("el => el.scrollWidth > el.clientWidth")

    form = page.get_by_role("form", name="Provision OIDC identity")
    form.get_by_label("OIDC subject").fill("future-browser-user")
    form.get_by_label("Initial role").select_option("viewer")
    form.get_by_role("button", name="Provision account").click()
    expect(page.locator("#settings-feedback")).to_contain_text("sign-in is still pending")
    new_user = users.filter(has_text="future-browser-user")
    expect(new_user).to_contain_text("Provisioned")
    expect(new_user).to_contain_text("No successful sign-in yet")
    new_user.get_by_label("Role for future-browser-user").select_option("user")
    role_confirmation = []
    page.once("dialog", lambda dialog: (role_confirmation.append(dialog.message), dialog.accept()))
    new_user.get_by_role("button", name="Change role").click()
    assert "future-browser-user" in role_confirmation[0]
    assert "Viewer to User" in role_confirmation[0]
    expect(new_user.get_by_label("Role for future-browser-user")).to_have_value("user")
    page.once("dialog", lambda dialog: dialog.accept())
    new_user.get_by_role("button", name="Disable account").click()
    expect(new_user).to_contain_text("Disabled")

    target = users.filter(has_text="session-target")
    session_confirmation = []
    page.once("dialog", lambda dialog: (session_confirmation.append(dialog.message), dialog.accept()))
    target.get_by_role("button", name="Revoke all sessions").click()
    assert "session-target" in session_confirmation[0]
    assert "all devices" in session_confirmation[0]
    expect(target.locator("td").nth(5)).to_have_text("0")

    page.context.add_cookies([{
        "name": "__Host-aslt_session", "value": "browser-viewer", "url": application[0],
        "secure": True, "httpOnly": True,
    }])
    page.reload()
    open_settings(page)
    expect(page.get_by_role("tab", name="Users", exact=True)).to_have_count(0)


def test_admin_users_can_review_and_revoke_personal_tokens(page, application):
    origin, _ = application
    page.context.add_cookies([{
        "name": "__Host-aslt_session", "value": "browser-admin", "url": origin,
        "secure": True, "httpOnly": True,
    }])
    page.goto(origin + "/")
    account = next(
        item for item in page.request.get(origin + "/api/v1/accounts").json()["items"]
        if item["identity"] == "admin"
    )
    created = page.request.post(
        origin + "/api/v1/personal-tokens",
        headers={"Origin": origin, "X-CSRF-Token": "csrf-admin"},
        data={"name": "admin-automation", "scopes": ["api:read"]},
    )
    assert created.status == 201
    secret = created.json()["token"]
    assert page.request.get(origin + "/api/v1/personal-tokens").json()["items"][0]["name"] == "admin-automation"
    assert page.request.get(
        origin + f"/api/v1/accounts/{account['id']}"
    ).json()["personal_token_count"] == 1

    page.reload()
    open_settings(page)
    page.get_by_role("tab", name="Users", exact=True).click()
    row = page.locator(".account-table tbody tr").filter(
        has=page.get_by_text("admin", exact=True)
    )
    expect(row).to_contain_text("1 active / 1 total")
    row.get_by_role("button", name="Review tokens").click()
    expect(row).to_contain_text("admin-automation")
    expect(row).to_contain_text(created.json()["prefix"])
    assert secret not in row.inner_text()

    page.once("dialog", lambda dialog: dialog.accept())
    row.get_by_role("button", name="Revoke token").click()
    expect(page.locator("#settings-feedback")).to_contain_text("Revoked admin-automation")
    assert page.request.get(
        origin + "/api/v1/recordings",
        headers={"Authorization": f"Bearer {secret}"},
    ).status == 401


def test_admin_users_can_load_accounts_beyond_first_page(page, application):
    page.context.add_cookies([{
        "name": "__Host-aslt_session", "value": "browser-admin", "url": application[0],
        "secure": True, "httpOnly": True,
    }])
    base_account = page.request.get(
        application[0] + "/api/v1/accounts?limit=200&offset=0"
    ).json()["items"][0]
    first_page = [base_account | {
        "id": f"fixture-account-{index}", "subject": f"subject-{index}",
        "identity": f"Fixture {index}", "display_name": f"Fixture {index}",
    } for index in range(200)]
    second_page = [base_account | {
        "id": "fixture-account-200", "subject": "subject-200",
        "identity": "Fixture 200", "display_name": "Fixture 200",
    }]

    def accounts_response(route):
        items = second_page if "offset=200" in route.request.url else first_page
        route.fulfill(json={"items": items})

    page.route("**/api/v1/accounts*", accounts_response)
    page.goto(application[0] + "/")
    open_settings(page)
    page.get_by_role("tab", name="Users", exact=True).click()
    users = page.locator(".account-table tbody tr")
    expect(users).to_have_count(200)
    more = page.get_by_role("button", name="Load more accounts")
    expect(more).to_be_visible()
    more.click()
    expect(users).to_have_count(201)
    expect(users.last).to_contain_text("Fixture 200")
    expect(more).to_be_hidden()


def test_api_access_reveals_personal_token_once_and_revokes_it(page, application):
    origin, _ = application
    page.goto(origin + "/")
    open_settings(page)
    access_tab = page.get_by_role("tab", name="API Access", exact=True)
    expect(access_tab).to_be_visible()
    access_tab.click()
    expect(page.get_by_role("link", name="Open API Console")).to_have_attribute("href", "/docs")

    form = page.get_by_role("form", name="Create personal API token")
    form.get_by_label("Token name").fill("browser automation")
    expect(form.get_by_label("Expires in")).to_have_value("90")
    expect(form.get_by_label("Permission preset")).to_have_value("read")
    expect(form.get_by_label("Read API data")).to_be_checked()
    form.get_by_label("Permission preset").select_option("node")
    expect(form.get_by_label("Read API data")).to_be_checked()
    expect(form.get_by_label("Manage Favorites and node controls")).to_be_checked()
    expect(form.get_by_label("Edit shared content")).not_to_be_checked()
    form.get_by_label("Permission preset").select_option("read")
    form.get_by_role("button", name="Create token").click()

    reveal = page.locator(".token-reveal")
    expect(reveal).to_contain_text("The complete secret will not be shown again")
    secret = page.get_by_label("New personal API token").input_value()
    assert secret.startswith("aslt_pat_")
    token = page.locator(".personal-token-item").filter(has_text="browser automation")
    expect(token).to_contain_text(secret[:18])
    assert token.locator(".token-status").inner_text().lower() == "active"
    assert page.request.get(
        origin + "/api/v1/recordings",
        headers={"Authorization": f"Bearer {secret}"},
    ).status == 200

    page.reload()
    open_settings(page)
    page.get_by_role("tab", name="API Access", exact=True).click()
    token = page.locator(".personal-token-item").filter(has_text="browser automation")
    expect(token).to_contain_text("active")
    expect(page.get_by_label("New personal API token")).to_have_count(0)
    page.once("dialog", lambda dialog: dialog.accept())
    token.get_by_role("button", name="Revoke token").click()
    expect(token).to_contain_text("revoked")
    assert page.request.get(
        origin + "/api/v1/recordings",
        headers={"Authorization": f"Bearer {secret}"},
    ).status == 401


def test_openapi_console_is_authenticated_and_uses_bundled_assets(page, application):
    origin, _ = application
    page.context.clear_cookies()
    anonymous = page.request.get(origin + "/openapi.json")
    assert anonymous.status == 401

    page.goto(origin + "/docs")

    expect(page).to_have_url(origin + "/welcome?next=%2Fdocs")
    page.context.add_cookies([{
        "name": "__Host-aslt_session", "value": "browser-viewer", "url": origin,
        "secure": True, "httpOnly": True,
    }])
    page.goto(origin + "/docs")
    expect(page.locator("#swagger-ui")).to_be_visible()
    expect(page.locator(".swagger-ui .scheme-container")).to_be_visible()
    expect(page.get_by_role("button", name="Authorize", exact=True)).to_be_visible()
    assert page.locator("script[src^='/static/swagger-ui/']").count() == 2


def test_swagger_authorize_uses_personal_token_for_permitted_read(page, application):
    origin, _ = application
    page.goto(origin + "/")
    open_settings(page)
    page.get_by_role("tab", name="API Access", exact=True).click()
    form = page.get_by_role("form", name="Create personal API token")
    form.get_by_label("Token name").fill("swagger-reader")
    form.get_by_role("button", name="Create token").click()
    secret = page.get_by_label("New personal API token").input_value()

    page.goto(origin + "/docs")
    page.get_by_role("button", name="Authorize", exact=True).click()
    dialog = page.locator(".dialog-ux")
    expect(dialog).to_be_visible()
    value = dialog.locator('input[type="text"]')
    expect(value).to_have_count(1)
    value.fill(secret)
    dialog.get_by_role("button", name="Authorize", exact=True).click()
    dialog.get_by_role("button", name="Close", exact=True).click()

    page.get_by_role("button", name="GET /api/v1/recordings").click()
    page.get_by_role("button", name="Try it out").click()
    with page.expect_response(
        lambda response: response.request.method == "GET"
        and response.url.split("?", 1)[0].endswith("/api/v1/recordings")
    ) as response:
        page.get_by_role("button", name="Execute").click()
    assert response.value.status == 200
