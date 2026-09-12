"""Exclusive playback uses real Chromium media state and in-memory WAV fixtures."""

import io
import wave

import pytest
from playwright.sync_api import expect


@pytest.fixture
def media(page):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000 * 60)
    payload = buffer.getvalue()
    page.route("**/api/v1/archive/recordings/*/audio", lambda route: route.fulfill(
        body=payload, content_type="audio/wav", headers={"Accept-Ranges": "bytes"}
    ))
    return payload


@pytest.fixture
def event_players(page, application, media):
    rows = [
        {"id": name, "source_path": name + ".wav", "audio_available": True,
         "audio_status": "available", "duration_seconds": 60, "included": True,
         "decision": "automatic", "tags": [], "started_at": "2026-09-05T12:00:00Z",
         "transcript": {"segments": [{"start": index + 2, "display_text": name}]}}
        for index, name in enumerate(("first", "second", "third", "fourth"))
    ]
    data = {"items": rows[:3], "has_more": True, "next_cursor": "next"}
    page.route("**/api/v1/sessions/playback", lambda route: route.fulfill(json={
        "id": "playback", "name": "Playback event", "status": "ended", "type": "Net",
        "tags": [], "description": "Playback regression", "source_label": "Fixture",
    }))
    for kind in ("markers", "detected", "checkins"):
        page.route(f"**/api/v1/sessions/playback/{kind}?*", lambda route: route.fulfill(
            json={"items": []}
        ))
    page.route("**/api/v1/sessions/playback/recordings?*", lambda route: route.fulfill(json=data))
    # Drive the real background refresh callback deterministically.
    page.add_init_script("""(() => {
        const interval = window.setInterval;
        window.setInterval = (callback, delay, ...args) => {
            if (delay === 5000) { window.refreshEvent = callback; return 0; }
            return interval(callback, delay, ...args);
        };
    })();""")
    page.goto(application[0] + "/events/playback")
    expect(page.locator("#recordings-list audio")).to_have_count(3)
    page.wait_for_function("() => [...document.querySelectorAll('audio')].every(a => a.readyState >= 2)")
    page.evaluate("window.originalPlayers = [...document.querySelectorAll('audio')]")
    return data, rows


def audio_for(page, name):
    return page.locator(f'[data-recording-id="{name}"] audio')


def assert_only(page, name):
    page.wait_for_function("""name => [...document.querySelectorAll('audio')].every(audio =>
        audio.paused === (audio.closest('[data-recording-id]').dataset.recordingId !== name))""", arg=name)
    page.wait_for_function("""name => document.querySelector(
        `[data-recording-id="${name}"] audio`).currentTime > 0""", arg=name)


def test_events_native_controls_and_timestamp_switching(page, event_players):
    page.evaluate("""() => {
        window.overlaps = [];
        for (const audio of originalPlayers) audio.addEventListener('playing', () => {
            if (originalPlayers.filter(a => !a.paused).length > 1) overlaps.push(audio.src);
        });
    }""")
    for name in ("first", "second", "third"):
        # Click Chromium's actual native play control, bypassing the seek helper.
        audio_for(page, name).click(position={"x": 18, "y": 27})
        assert_only(page, name)
    for name, offset in (("first", 2), ("second", 3), ("second", 3)):
        page.locator(f'[data-recording-id="{name}"]').get_by_role(
            "button", name=f"{offset:.2f}s", exact=True
        ).click()
        assert_only(page, name)
        page.wait_for_function("""({name, offset}) => {
            const audio = document.querySelector(`[data-recording-id="${name}"] audio`);
            return !audio.seeking && audio.currentTime >= offset && audio.currentTime < offset + 2;
        }""", arg={"name": name, "offset": offset})
    assert page.evaluate("overlaps") == []
    assert page.evaluate("originalPlayers[0].currentTime") >= 2


def test_events_refresh_rebuild_load_more_removal_and_navigation(page, application, event_players):
    data, rows = event_players
    audio_for(page, "first").click(position={"x": 18, "y": 27})
    assert_only(page, "first")
    rows[0]["transcript"]["segments"][0]["display_text"] = "Updated transcript"
    page.evaluate("refreshEvent()")
    expect(page.locator('[data-recording-id="first"]')).to_contain_text("Updated transcript")
    assert page.evaluate("originalPlayers[0] === document.querySelector('audio')")
    assert_only(page, "first")
    data["items"] = [rows[3]]
    data["has_more"] = False
    page.locator("#more-recordings").click()
    expect(page.locator("audio")).to_have_count(4)
    page.locator('[data-recording-id="fourth"]').get_by_role("button", name="5.00s").click()
    assert_only(page, "fourth")
    page.evaluate("window.removedPlayer = document.querySelector('[data-recording-id=\"fourth\"] audio')")
    data["items"] = []
    page.evaluate("refreshEvent()")
    expect(audio_for(page, "fourth")).to_have_count(0)
    page.wait_for_function("() => removedPlayer.paused")
    assert page.evaluate("originalPlayers.every(a => a.paused)")
    # Background refresh replaces only lastKeys (the appended fourth page),
    # preserving players from previously loaded pages, including second.
    expect(audio_for(page, "second")).to_be_visible()
    # Explicit refresh rebuilds the list; discarded players must also stop.
    audio_for(page, "second").click(position={"x": 18, "y": 27})
    assert_only(page, "second")
    data["items"] = rows[:3]
    page.locator("#refresh-recordings").click()
    page.wait_for_function("() => originalPlayers.every(a => a.paused)")
    audio_for(page, "first").click(position={"x": 18, "y": 27})
    assert_only(page, "first")
    # Capture real pagehide state in sessionStorage before the document is lost.
    page.evaluate("""() => addEventListener('pagehide', () => sessionStorage.setItem(
        'leftPaused', String([...document.querySelectorAll('audio')].every(a => a.paused))))""")
    page.get_by_role("link", name="Archive", exact=True).click()
    expect(page).to_have_url(application[0] + "/archive")
    assert page.evaluate("sessionStorage.getItem('leftPaused')") == "true"


def test_events_delayed_load_and_old_play_completion_cannot_reclaim_audio(page, event_players, media):
    delayed = []
    page.route("**/slow-audio", lambda route: delayed.append(route))
    page.evaluate("""() => {
        originalPlayers[0].src = '/slow-audio';
        const play = originalPlayers[0].play.bind(originalPlayers[0]);
        originalPlayers[0].play = () => {
            const started = play();
            return new Promise((resolve, reject) => {
                started.catch(() => {});
                window.finishOldPlay = resolve;
            });
        };
    }""")
    page.locator('[data-recording-id="first"]').get_by_role("button", name="2.00s").click()
    page.wait_for_function("() => originalPlayers[0].readyState === 0")
    page.locator('[data-recording-id="second"]').get_by_role("button", name="3.00s").click()
    page.locator('[data-recording-id="third"]').get_by_role("button", name="4.00s").click()
    assert_only(page, "third")
    assert delayed
    delayed[0].fulfill(body=media, content_type="audio/wav")
    page.evaluate("finishOldPlay()")
    page.wait_for_function("() => originalPlayers[0].readyState >= 2")
    assert_only(page, "third")
    assert page.evaluate("originalPlayers[0].paused && originalPlayers[1].paused")


def test_events_end_error_rejection_and_removed_pending_player(page, event_players):
    page.locator('[data-recording-id="first"]').get_by_role("button", name="2.00s").click()
    assert_only(page, "first")
    audio_for(page, "first").evaluate("a => { a.currentTime = a.duration - 0.1; }")
    page.wait_for_function("() => originalPlayers[0].ended && originalPlayers[0].paused")
    page.route("**/broken-audio", lambda route: route.fulfill(body=b"invalid", content_type="audio/wav"))
    audio_for(page, "second").evaluate("a => { a.src = '/broken-audio'; }")
    expect(audio_for(page, "second")).to_have_count(0)
    expect(page.locator('[data-recording-id="second"]')).to_contain_text("Audio unavailable")
    page.evaluate("""() => {
        originalPlayers[2].play = () => Promise.reject(new DOMException('Denied', 'NotAllowedError'));
    }""")
    page.locator('[data-recording-id="third"]').get_by_role("button", name="4.00s").click()
    assert page.evaluate("originalPlayers.every(a => a.paused)")
    page.evaluate("""() => {
        delete originalPlayers[2].play;
        Playback.play(originalPlayers[2], {offset: 4}).catch(() => {});
        originalPlayers[2].remove();
    }""")
    page.wait_for_function("() => originalPlayers[2].paused")


@pytest.fixture
def mention_players(page, application, media):
    mentions = [{"mention_id": name, "recording_id": name, "audio_available": True,
                 "start_offset": offset, "review_status": "detected"}
                for name, offset in (("first", 2), ("second", 7), ("third", 12))]
    page.route("**/api/v1/callsigns/KM7GHS/mentions?*", lambda route: route.fulfill(json={
        "items": mentions, "has_more": False
    }))
    page.goto(application[0] + "/callsigns/KM7GHS")
    expect(page.locator("#history .recording")).to_have_count(3)


def test_callsign_switch_seek_pause_end_error_and_history_removal(page, mention_players):
    for name, offset in (("first", 2), ("second", 7), ("third", 12)):
        card = page.locator(f'[data-mention-id="{name}"]')
        card.get_by_role("button", name="Play from mention", exact=True).click()
        page.wait_for_function("offset => !player.paused && player.currentTime >= offset", arg=offset)
        assert page.evaluate("player.currentTime") < offset + 2
        expect(page.get_by_role("button", name="Playing", exact=True)).to_have_count(1)
        expect(card.get_by_role("button", name="Playing", exact=True)).to_be_visible()
    page.evaluate("player.pause()")
    expect(page.get_by_role("button", name="Playing", exact=True)).to_have_count(0)
    third = page.locator('[data-mention-id="third"] button').first
    third.click()
    page.wait_for_function("() => !player.paused && player.currentTime >= 12")
    page.evaluate("player.currentTime = player.duration - 0.1")
    page.wait_for_function("() => player.ended")
    expect(third).to_have_text("Play from mention")
    third.click()
    page.wait_for_function("() => !player.paused && player.currentTime >= 12")
    page.evaluate("loadHistory(true)")
    assert page.evaluate("player.paused")
    expect(page.get_by_role("button", name="Playing", exact=True)).to_have_count(0)
    page.route("**/api/v1/archive/recordings/third/audio", lambda route: route.fulfill(
        body=b"invalid", content_type="audio/wav"
    ))
    page.evaluate("player.src = ''")
    third.click()
    page.wait_for_function("() => player.error !== null")
    expect(third).to_have_text("Play from mention")
    # A transient media failure must be retryable without navigating or changing URL.
    page.unroute("**/api/v1/archive/recordings/third/audio")
    third.click()
    page.wait_for_function("() => !player.error && !player.paused && player.currentTime >= 12")
    expect(third).to_have_text("Playing")


def test_callsign_delayed_metadata_and_rejected_old_play(page, mention_players, media):
    delayed = []
    page.route("**/api/v1/archive/recordings/first/audio", lambda route: delayed.append(route))
    page.evaluate("""() => {
        const play = player.play.bind(player);
        player.play = () => {
            player.play = play;
            play().catch(() => {});
            return new Promise((resolve, reject) => { window.rejectOld = reject; });
        };
    }""")
    page.locator('[data-mention-id="first"]').get_by_role("button", name="Play from mention").click()
    page.locator('[data-mention-id="second"]').get_by_role("button", name="Play from mention").click()
    page.wait_for_function("() => !player.paused && player.currentTime >= 7 && player.readyState >= 2")
    assert delayed
    delayed[0].fulfill(body=media, content_type="audio/wav")
    page.evaluate("rejectOld(new DOMException('Interrupted', 'AbortError'))")
    expect(page.locator('[data-mention-id="second"]')).to_contain_text("Playing")
    assert page.evaluate("player.currentSrc.endsWith('/second/audio') && !player.paused")
    assert 7 <= page.evaluate("player.currentTime") < 10


def test_archive_seek_and_evidence_share_coordination(page, application, media):
    origin, ids = application
    page.goto(origin + "/archive/recordings/" + ids["KM7GHS"])
    expect(page.locator("audio")).to_have_count(1)
    # Compete with a registered custom player to verify the Archive entry points
    # use the same coordinator, while retaining native controls and exact offsets.
    page.evaluate("""() => {
        window.other = Playback.register(new Audio('/api/v1/archive/recordings/second/audio'), {persistent: true});
    }""")
    for selector in (".segment-seek", "#callsign-evidence button"):
        page.evaluate("Playback.play(other)")
        page.wait_for_function("() => !other.paused && other.currentTime > 0")
        page.locator(selector).first.click()
        page.wait_for_function("""() => other.paused && !document.querySelector('audio').paused &&
            document.querySelector('audio').currentTime >= 2""")
    page.evaluate("Playback.play(other)")
    page.wait_for_function("() => document.querySelector('audio').paused && !other.paused")
    page.locator("audio").click(position={"x": 18, "y": 27})
    page.wait_for_function("() => other.paused && !document.querySelector('audio').paused")
