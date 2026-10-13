import pytest
from playwright.sync_api import expect


@pytest.fixture
def device(browser, application):
    origin, _ = application
    contexts = []

    def open_device(token):
        context = browser.new_context(ignore_https_errors=True, locale="en-US")
        contexts.append(context)
        context.add_cookies([{
            "name": "__Host-aslt_session", "value": token, "url": origin,
            "secure": True, "httpOnly": True, "sameSite": "Lax",
        }])
        context.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(origin + "/") else route.abort(),
        )
        return context.new_page()

    yield open_device
    for context in contexts:
        context.close()


def test_anonymous_visit_shows_landing_without_provider_redirect(browser, application):
    origin, _ = application
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.goto(origin + "/archive?q=net")
    expect(page).to_have_url(origin + "/welcome?next=%2Farchive%3Fq%3Dnet")
    expect(page.get_by_role("link", name="Sign in")).to_have_attribute(
        "href", "/auth/login?next=%2Farchive%3Fq%3Dnet"
    )
    assert "Transcript" not in page.content()
    context.close()


def test_logout_returns_to_landing_without_reload_and_keeps_other_device(device, application):
    origin, _ = application
    first, second = device("browser-device-a"), device("browser-device-b")
    first.goto(origin + "/archive")
    second.goto(origin + "/archive")
    first.get_by_role("button", name="Sign out").click()
    expect(first).to_have_url(origin + "/welcome?reason=signed_out")
    expect(first.get_by_text("You are signed out")).to_be_visible()
    second.reload()
    expect(second).to_have_url(origin + "/archive")
    expect(second.get_by_role("heading", name="Radio Archive")).to_be_visible()


def test_revoked_session_is_detected_on_return_to_tab(device, application):
    origin, _ = application
    victim, other = device("browser-device-revoked"), device("browser-device-revoked")
    victim.goto(origin + "/events")
    other.goto(origin + "/events")
    other.get_by_role("button", name="Sign out").click()
    expect(other).to_have_url(origin + "/welcome?reason=signed_out")
    victim.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(victim).to_have_url(origin + "/welcome?reason=expired&next=%2Fevents")
    expect(victim.get_by_text("Your session expired")).to_be_visible()


def test_outage_is_not_treated_as_logout(device, application):
    origin, _ = application
    page = device("browser-device-b")
    page.goto(origin + "/callsigns")
    page.route("**/api/v1/auth/me", lambda route: route.abort())
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(1000)
    expect(page).to_have_url(origin + "/callsigns")
