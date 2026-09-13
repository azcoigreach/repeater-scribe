"""Favorites read recovery must never own or replay a node-control result."""

import pytest
from playwright.sync_api import expect


@pytest.fixture
def favorites_reads(page, application):
    state = {"mode": "success", "calls": 0, "pending": []}
    items = [
        {"id": "first", "target_identifier": "200"},
        {"id": "second", "target_identifier": "300"},
    ]

    def read(route):
        state["calls"] += 1
        mode = state["mode"]
        if mode == "hold":
            state["pending"].append(route)
        elif mode == "network":
            route.abort()
        elif mode == "malformed":
            route.fulfill(body="not json", content_type="application/json")
        elif mode == "schema":
            route.fulfill(json={"items": [None]})
        elif mode == "502":
            route.fulfill(status=502, body="Bad Gateway")
        else:
            route.fulfill(json={"items": [] if mode == "empty" else items})

    page.route("**/api/v1/nodes/*/favorites", read)
    # Capture only Favorites' scheduler and timeout, leaving other page work alone.
    page.add_init_script("""(() => {
        const timeout = window.setTimeout;
        window.setTimeout = (callback, delay, ...args) => {
            if (delay === 10000 && callback.name === 'loadFavorites') {
                window.retryFavorites = callback; return 0;
            }
            if (delay === 8000) { window.timeoutFavorites = callback; return 0; }
            return timeout(callback, delay, ...args);
        };
    })();""")
    commands = []

    def command(route):
        commands.append(route.request.url)
        route.fulfill(status=502, json={"detail": "Command failed for fixture"})

    page.route("**/ui/node/*/command", command)
    page.route("**/ui/node/*/function", command)
    yield state, items, commands, application[0]


def open_favorites(page, origin):
    page.goto(origin + "/")
    page.evaluate("activatePanel('favorites')")
    page.wait_for_function("() => window.retryFavorites !== undefined")


@pytest.mark.parametrize("failure", ["502", "network", "malformed", "schema"])
def test_retains_list_menu_and_command_error_until_read_recovers(page, favorites_reads, failure):
    state, _, commands, origin = favorites_reads
    open_favorites(page, origin)
    toggle = page.get_by_role("button", name="Choose connection mode for node 200")
    toggle.click()
    state["mode"] = failure
    page.evaluate("retryFavorites()")
    status = page.locator("#favorites-status")
    expect(status).to_contain_text("Showing last loaded Favorites; data may be stale.")
    expect(page.locator("#favorites td strong")).to_have_text(["200", "300"])
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(toggle).to_be_focused()
    # An actual explicit command failure belongs to Node Controls, even when
    # the older Favorites read error subsequently recovers.
    page.evaluate("runCommand('Connect node', '200')")
    expect(page.locator("#control-result")).to_have_text("Command failed for fixture")
    calls = state["calls"]
    page.evaluate("retryFavorites()")
    assert state["calls"] == calls + 1
    expect(status).to_be_visible()
    state["mode"] = "success"
    page.evaluate("retryFavorites()")
    expect(status).to_be_hidden()
    expect(page.locator("#control-result")).to_have_text("Command failed for fixture")
    expect(page.locator("#favorites td strong")).to_have_text(["200", "300"])
    expect(toggle).to_have_attribute("aria-expanded", "true")
    assert len(commands) == 1


def test_first_failure_is_not_empty_and_persistent_failure_stays_visible(page, favorites_reads):
    state, _, commands, origin = favorites_reads
    state["mode"] = "502"
    open_favorites(page, origin)
    status = page.locator("#favorites-status")
    expect(status).to_contain_text("No Favorites data loaded.")
    expect(page.locator("#favorites")).to_contain_text("have not been loaded")
    for _ in range(3):
        page.evaluate("retryFavorites()")
        expect(status).to_contain_text("HTTP 502")
    state["mode"] = "empty"
    page.evaluate("retryFavorites()")
    expect(status).to_be_hidden()
    expect(page.locator("#favorites")).to_contain_text("No favorite nodes yet")
    assert commands == []


def test_slow_read_is_shared_times_out_and_recovers(page, favorites_reads):
    state, _, commands, origin = favorites_reads
    open_favorites(page, origin)
    state["mode"] = "hold"
    before = state["calls"]
    page.evaluate("() => { window.slowRead = loadFavorites(); loadFavorites(); loadFavorites(); }")
    page.wait_for_function("() => favoritesRequest !== null")
    page.evaluate("() => { timeoutFavorites(); return slowRead; }")
    assert state["calls"] == before + 1
    expect(page.locator("#favorites-status")).to_contain_text("request timed out")
    state["mode"] = "success"
    page.evaluate("retryFavorites()")
    expect(page.locator("#favorites-status")).to_be_hidden()
    expect(page.locator("#favorites td strong")).to_have_text(["200", "300"])
    assert commands == []


@pytest.mark.parametrize("old_result", ["success", "failure"])
def test_obsolete_completion_cannot_replace_new_home_data_or_status(
    page, favorites_reads, old_result
):
    _, _, commands, origin = favorites_reads
    open_favorites(page, origin)
    # Delay a fetch completion even past abort, covering response/body races.
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.fetch = (url, options) => {
            if (String(url).includes('/favorites')) {
                window.fetch = originalFetch;
                return new Promise(resolve => { window.finishOldFavorite = resolve; });
            }
            return originalFetch(url, options);
        };
        window.oldRead = loadFavorites();
        document.querySelector('#desktop').dataset.controlledNode = '999';
    }""")
    page.evaluate("loadFavorites()")
    page.evaluate("""async result => {
        finishOldFavorite(new Response(JSON.stringify({items: [
            {id: 'obsolete', target_identifier: '777'}
        ]}), {status: result === 'failure' ? 502 : 200}));
        await oldRead;
    }""", old_result)
    expect(page.locator("#favorites td strong")).to_have_text(["200", "300"])
    expect(page.locator("#favorites-status")).to_be_hidden()
    assert commands == []


def test_recovery_preserves_active_topology_drag(page, favorites_reads):
    state, items, commands, origin = favorites_reads
    items[0]["topology"] = [{"identifier": "300"}]
    open_favorites(page, origin)
    # Stub only graph reads/crawls; drag and deferred rendering remain real UI code.
    graph = {"root": "200", "nodes": [], "edges": []}
    page.route("**/ui/nodes/*/topology/*/crawl?*", lambda route: route.fulfill(json=graph))
    page.route("**/api/v1/nodes/*/topology?*", lambda route: route.fulfill(json=graph))
    page.evaluate("openTopology('first')")
    page.wait_for_function("() => topologyGraph !== null")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    bubble = page.locator('#topology-chart .topology-node[data-node-id="300"]')
    expect(bubble).to_be_visible()
    bubble.scroll_into_view_if_needed()
    position = bubble.locator(".topology-bubble").bounding_box()
    assert position
    bubble.locator(".topology-bubble").hover()
    page.mouse.down()
    assert page.evaluate("topologyInteractionActive")
    page.mouse.move(position["x"] + position["width"] / 2 + 15, position["y"] + position["height"] / 2 + 15)
    page.evaluate("window.draggedBubble = document.querySelector('.topology-node[data-node-id=\"300\"]')")
    assert page.evaluate("topologyInteractionActive")
    state["mode"] = "502"
    page.evaluate("retryFavorites()")
    state["mode"] = "success"
    page.evaluate("retryFavorites()")
    assert page.evaluate("draggedBubble.isConnected && topologyInteractionActive")
    dragged_position = bubble.get_attribute("transform")
    page.mouse.up()
    page.wait_for_function("() => !topologyInteractionActive && !topologyRenderPending")
    expect(bubble).to_have_attribute("transform", dragged_position)
    assert commands == []
