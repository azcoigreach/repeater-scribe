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


def test_changed_session_cookie_reloads_workspace_with_new_csrf(device, application):
    origin, _ = application
    page = device("browser-session-refresh-a")
    page.goto(origin + "/archive")
    expect(page.locator('meta[name="csrf-token"]')).to_have_attribute(
        "content", "csrf-session-refresh-a"
    )
    page.context.add_cookies([{
        "name": "__Host-aslt_session", "value": "browser-session-refresh-b", "url": origin,
        "secure": True, "httpOnly": True, "sameSite": "Lax",
    }])

    page.evaluate("window.dispatchEvent(new Event('focus'))")

    expect(page.locator('meta[name="csrf-token"]')).to_have_attribute(
        "content", "csrf-session-refresh-b"
    )


def test_confirmed_logout_stops_playback_and_inerts_workspace(device, application):
        origin, _ = application
        page = device("browser-session-cleanup")
        page.goto(origin + "/events")
        page.add_script_tag(url=origin + "/static/playback.js")
        page.evaluate("""() => {
            const listeners = new Map();
            window.reviewPlayer = {
                paused: false, ended: false, error: null, isConnected: false,
                addEventListener(type, handler) { listeners.set(type, handler); },
                removeEventListener(type) { listeners.delete(type); },
                pause() { this.paused = true; },
            };
            Playback.register(window.reviewPlayer, { persistent: true });
            window.addEventListener('pagehide', () => sessionStorage.setItem('auth-cleanup', JSON.stringify({
                paused: window.reviewPlayer.paused,
                inert: document.body.inert,
            })));
        }""")

        page.get_by_role("button", name="Sign out").click()

        expect(page).to_have_url(origin + "/welcome?reason=signed_out")
        cleanup = page.evaluate("JSON.parse(sessionStorage.getItem('auth-cleanup'))")
        assert cleanup == {"paused": True, "inert": True}


def test_probe_timeout_allows_a_later_auth_check(device, application):
        origin, _ = application
        page = device("browser-session-timeout")
        page.add_init_script("""(() => {
            const realFetch = window.fetch.bind(window);
            window.__probeMode = 'normal';
            window.__probeAborted = 0;
            window.fetch = (input, init = {}) => {
                if (new URL(typeof input === 'string' ? input : input.url, location.origin).pathname === '/api/v1/auth/me') {
                    if (window.__probeMode === 'hang') {
                        return new Promise((resolve, reject) => init.signal.addEventListener('abort', () => {
                            window.__probeAborted++;
                            reject(new DOMException('Aborted', 'AbortError'));
                        }, { once: true }));
                    }
                    if (window.__probeMode === 'unauthorized') return Promise.resolve(new Response('{}', { status: 401 }));
                }
                return realFetch(input, init);
            };
        })();""")
        page.goto(origin + "/callsigns")
        page.route("**/welcome*", lambda route: route.fulfill(
                status=200, content_type="text/html", body="<title>Welcome</title>"
        ))
        page.wait_for_timeout(100)
        page.evaluate("window.__probeMode = 'hang'; window.dispatchEvent(new Event('focus'))")
        page.wait_for_timeout(5500)
        assert page.evaluate("window.__probeAborted") == 1
        expect(page).to_have_url(origin + "/callsigns")
        page.wait_for_timeout(100)
        page.evaluate("window.__probeMode = 'unauthorized'; window.dispatchEvent(new Event('focus'))")

        expect(page).to_have_url(origin + "/welcome?reason=expired&next=%2Fcallsigns")


def test_periodic_auth_check_detects_revoked_session(device, application):
    origin, _ = application
    page = device("browser-session-periodic")
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.add_init_script("""(() => {
      const nativeSetInterval = window.setInterval.bind(window);
      window.setInterval = (callback, delay, ...args) => {
        if (delay === 60000) window.__authCheckTick = callback;
        return nativeSetInterval(callback, delay, ...args);
      };
    })();""")
    page.goto(origin + "/callsigns")
    page.route("**/welcome*", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<title>Welcome</title>"
    ))
    probes = []

    def reject_probe(route):
        probes.append(route.request.url)
        route.fulfill(status=401, json={"detail": "expired"})

    page.route("**/api/v1/auth/me", reject_probe)

    assert page.evaluate("typeof window.__authCheckTick") == "function"
    page.evaluate(
        "Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'}); "
        "window.__authCheckTick()"
    )
    page.wait_for_timeout(100)
    assert probes
    assert not page_errors, page_errors

    expect(page).to_have_url(origin + "/welcome?reason=expired&next=%2Fcallsigns")
