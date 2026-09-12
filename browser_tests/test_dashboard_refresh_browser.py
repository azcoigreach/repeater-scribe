"""Dashboard refresh preserves active controls using mocked data and generated audio."""

import io
import wave

import pytest
from playwright.sync_api import expect


@pytest.fixture
def favorites_dashboard(page, application):
    data = {
        "items": [
            {"id": "first", "target_identifier": "200", "reported_busy_percent": 10},
            {"id": "second", "target_identifier": "300", "reported_busy_percent": 20},
        ]
    }
    commands = []
    page.route("**/api/v1/nodes/*/favorites", lambda route: route.fulfill(json=data))
    page.route(
        "**/api/v1/node/status",
        lambda route: route.fulfill(json={"connections": [], "ami_connected": True}),
    )

    def command(route):
        commands.append((route.request.url, route.request.post_data_json))
        route.fulfill(json={"pending_confirmation": False})

    page.route("**/ui/node/*/command", command)
    page.goto(application[0] + "/")
    page.evaluate("async () => { await loadFavorites(); activatePanel('favorites'); }")
    return data, commands


def test_favorite_menu_focus_selection_and_dismissal_survive_refresh(page, favorites_dashboard):
    data, commands = favorites_dashboard
    toggle = page.get_by_role("button", name="Choose connection mode for node 200")
    option = page.locator('.favorite-connect-option[data-target="200"]').filter(
        has_text="Monitor local"
    )
    toggle.click()
    for busy in (31, 47):
        next(item for item in data["items"] if item["target_identifier"] == "200")[
            "reported_busy_percent"
        ] = busy
        data["items"].reverse()
        page.evaluate("async () => { await loadFavorites(); await loadNodeStatus(); }")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(toggle if busy == 31 else option).to_be_focused()
        expect(page.locator("#favorites")).to_contain_text(f"{busy}%")
        option.focus()
    home = page.locator("#desktop").get_attribute("data-controlled-node")
    option.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.wait_for_function("() => document.querySelector('#control-result').textContent.includes('sent')")
    assert len(commands) == 1
    assert commands[0][0].endswith(f"/ui/node/{home}/command")
    assert commands[0][1] == {
        "name": "Connect local monitor", "target": "200", "confirmed": True
    }

    for dismissal in ("escape", "outside", "toggle"):
        toggle.click()
        if dismissal == "escape":
            toggle.press("Escape")
        elif dismissal == "outside":
            page.locator("#favorites td strong").first.click()
        else:
            toggle.click()
        page.evaluate("loadFavorites()")
        expect(toggle).to_have_attribute("aria-expanded", "false")
    assert len(commands) == 1


@pytest.mark.parametrize("change", ["removed", "target", "home", "connected"])
def test_favorite_refresh_discards_invalid_menu(page, favorites_dashboard, change):
    data, commands = favorites_dashboard
    page.get_by_role("button", name="Choose connection mode for node 200").click()
    option = page.locator('.favorite-connect-option[data-target="200"]').first
    option.focus()
    if change == "removed":
        data["items"].pop(0)
    elif change == "target":
        data["items"][0]["target_identifier"] = "400"
    elif change == "home":
        page.locator("#desktop").evaluate("node => { node.dataset.controlledNode = '999'; }")
    else:
        page.evaluate("renderNodeSnapshot({connections: [{identifier: '200', keyed: true}]})")
    page.evaluate("loadFavorites()")
    expect(page.locator('.favorite-connect-options:not([hidden])')).to_have_count(0)
    expect(page.locator('.favorite-connect-toggle[aria-expanded="true"]')).to_have_count(0)
    assert commands == []
    if change == "connected":
        disconnect = page.locator('.favorite-connect[data-target="200"]')
        expect(disconnect).to_have_text("Disconnect")
        expect(disconnect.locator("xpath=ancestor::tr")).to_have_class("favorite-row talking")
        page.evaluate("renderNodeSnapshot({connections: []})")
        expect(page.get_by_role("button", name="Choose connection mode for node 200")).to_have_attribute(
            "aria-expanded", "false"
        )


@pytest.fixture
def playback_dashboard(page, application):
    # Long, valid media served entirely within the browser fixture; no archive writes.
    media = {}
    for name, duration in (("first", 60), ("second", 3), ("new", 60)):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000 * duration)
        media[f"{name}.wav"] = buffer.getvalue()
    page.route(
        "**/refresh-audio/*.wav",
        lambda route: route.fulfill(
            body=media[route.request.url.rsplit("/", 1)[1]], content_type="audio/wav"
        ),
    )
    items = [
        {
            "source_path": f"{name}.wav", "audio_url": f"/refresh-audio/{name}.wav",
            "source_id": "fixture-root",
            "status": "completed", "callsigns": [],
            "transcript": {"display_text": f"Transcript {name}"},
        }
        for name in ("first", "second", "new")
    ]
    data = {"items": items[:2]}
    page.route("**/api/v1/recordings?*", lambda route: route.fulfill(json=data))
    page.goto(application[0] + "/")
    page.evaluate("async () => { await loadJobs(); activatePanel('transcripts'); }")
    page.evaluate("""() => {
        window.mediaEvents = [];
        for (const type of ['loadstart', 'play', 'pause', 'ended', 'error']) {
            player.addEventListener(type, () => mediaEvents.push(type));
        }
    }""")
    return data, items


def playback_button(page, name):
    return page.locator(f'[data-source-path="{name}.wav"] button').first


def test_playback_refresh_reorder_filter_pause_switch_and_end(page, playback_dashboard):
    data, items = playback_dashboard
    first = playback_button(page, "first")
    second = playback_button(page, "second")
    first.click()
    page.wait_for_function("() => !player.paused && player.currentTime > 0.1")
    expect(first).to_have_text("❚❚ Playing")
    before = page.evaluate("({time: player.currentTime, src: player.src, events: [...mediaEvents]})")
    for order in ([items[2], items[1], items[0]], [items[0], items[2], items[1]]):
        data["items"] = order
        # Assignment of an ingestion job must not change the root/path identity.
        items[0]["id"] = "ingested-first"
        items[0]["transcript"]["display_text"] += " updated"
        page.evaluate("loadJobs()")
        expect(first).to_have_text("❚❚ Playing")
        expect(first).to_have_attribute("aria-label", "Pause first.wav")
        expect(second).to_have_text("▶ Play audio")
        expect(page.locator('[data-source-path="first.wav"]')).to_contain_text(
            items[0]["transcript"]["display_text"]
        )
    # Filtering the playing recording out must not transfer state to another card.
    data["items"] = [items[1]]
    page.evaluate("loadJobs()")
    expect(first).to_have_count(0)
    expect(second).to_have_text("▶ Play audio")
    data["items"] = []
    page.evaluate("loadJobs()")
    expect(page.locator("#recordings")).to_contain_text("No recordings match")
    data["items"] = [items[1], items[0]]
    # Existing playback remains controllable even if refreshed source availability changes.
    items[0]["audio_url"] = None
    page.evaluate("loadJobs()")
    expect(first).to_have_text("❚❚ Playing")
    expect(first).to_be_enabled()
    page.wait_for_function("time => player.currentTime > time + 0.2", arg=before["time"])
    assert page.evaluate("player.src") == before["src"]
    assert page.evaluate("mediaEvents") == before["events"]
    first.click()
    page.wait_for_function("() => player.paused")
    expect(first).to_have_text("▶ Play audio")
    expect(first).to_be_disabled()

    items[0]["audio_url"] = "/refresh-audio/first.wav"
    page.evaluate("loadJobs()")
    first.click()
    expect(first).to_have_text("❚❚ Playing")
    page.evaluate("loadJobs()")
    second.click()
    expect(first).to_have_text("▶ Play audio")
    expect(second).to_have_text("❚❚ Playing")
    page.wait_for_function("() => player.currentSrc.endsWith('/second.wav') && player.readyState >= 2")
    page.evaluate("loadJobs()")
    # The shorter second fixture naturally ends on the replacement control.
    page.wait_for_function("() => player.ended")
    expect(second).to_have_text("▶ Play audio")
    page.evaluate("loadJobs()")
    expect(second).to_have_text("▶ Play audio")


def test_playback_error_clears_refreshed_control(page, playback_dashboard):
    first = playback_button(page, "first")
    first.click()
    page.wait_for_function("() => !player.paused && player.currentTime > 0")
    page.evaluate("loadJobs()")
    page.route(
        "**/refresh-audio/broken.wav",
        lambda route: route.fulfill(body=b"invalid wave", content_type="audio/wav"),
    )
    page.evaluate("player.src = '/refresh-audio/broken.wav'; player.load()")
    page.wait_for_function("() => player.error !== null")
    expect(first).to_have_text("▶ Play audio")
    page.evaluate("loadJobs()")
    expect(first).to_have_text("▶ Play audio")


def test_rejected_old_play_request_does_not_clear_new_playback(page, playback_dashboard):
    page.evaluate("""() => {
        const originalPlay = player.play.bind(player);
        player.play = () => {
            player.play = originalPlay;
            return new Promise((resolve, reject) => { window.rejectOldPlay = reject; });
        };
    }""")
    playback_button(page, "first").click()
    page.evaluate("loadJobs()")
    second = playback_button(page, "second")
    second.click()
    page.wait_for_function("() => !player.paused && player.currentTime > 0")
    page.evaluate("loadJobs()")
    page.evaluate("rejectOldPlay(new DOMException('Interrupted', 'AbortError'))")
    expect(second).to_have_text("❚❚ Playing")
    expect(playback_button(page, "first")).to_have_text("▶ Play audio")


def test_rejected_current_play_request_clears_refreshed_control(page, playback_dashboard):
    page.evaluate("""() => {
        player.play = () => new Promise((resolve, reject) => { window.rejectPlay = reject; });
    }""")
    first = playback_button(page, "first")
    first.click()
    page.evaluate("loadJobs()")
    page.evaluate("rejectPlay(new DOMException('Playback denied', 'NotAllowedError'))")
    expect(first).to_have_text("▶ Play audio")
    expect(first).to_have_attribute("aria-label", "Play first.wav")


def test_duplicate_filenames_in_distinct_roots_do_not_share_playback(page, playback_dashboard):
    data, items = playback_dashboard
    items[0]["source_path"] = items[1]["source_path"] = "same.wav"
    items[0]["source_id"] = "root-one"
    items[1]["source_id"] = "root-two"
    page.evaluate("loadJobs()")
    first = page.locator('.recording').filter(has_text="Transcript first").locator('button').first
    second = page.locator('.recording').filter(has_text="Transcript second").locator('button').first
    first.click()
    page.wait_for_function("() => !player.paused && player.currentTime > 0")
    data["items"] = [items[1], items[0]]
    page.evaluate("loadJobs()")
    expect(first).to_have_text("❚❚ Playing")
    expect(second).to_have_text("▶ Play audio")
    data["items"] = [items[1]]
    page.evaluate("loadJobs()")
    expect(second).to_have_text("▶ Play audio")
    data["items"] = [items[1], items[0]]
    page.evaluate("loadJobs()")
    expect(first).to_have_text("❚❚ Playing")
    second.click()
    page.wait_for_function("() => !player.paused && player.currentSrc.endsWith('/second.wav')")
    expect(first).to_have_text("▶ Play audio")
    expect(second).to_have_text("❚❚ Playing")
