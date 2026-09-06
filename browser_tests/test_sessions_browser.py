"""Live operation and archive reconstruction with real session APIs and SQLite."""

from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect


def start_form(page, origin, historical=False):
    page.goto(origin + "/events")
    page.locator("#historical-event" if historical else "#start-event").click()
    expect(page.locator("#event-form [name=source_id] option").first).to_be_attached()
    page.locator("#event-form [name=name]").fill(
        "Groovy Late Shift" if not historical else "Yesterday net"
    )
    page.locator("#event-form [name=started_at]").fill("2026-09-05T11:59")
    if historical:
        page.locator("#event-form [name=ended_at]").fill("2026-09-05T12:03")


def test_live_groovy_accumulation_markers_checkin_end_reopen_and_seek(page, application):
    origin, ids = application
    start_form(page, origin)
    page.locator("#event-form [name=tags]").fill("groovy, late-shift")
    page.locator("#save-event").click()
    expect(page.locator("#event-detail")).to_be_visible()
    expect(page.locator("#event-title")).to_have_text("Groovy Late Shift")
    expect(page.locator("#recordings-list .event-card")).to_have_count(1)
    event_url = page.url
    # This fixture endpoint commits a newly discovered recording via the same
    # catalog hook used by ingestion. The browser must accumulate it by polling.
    response = page.request.post(
        origin + "/browser/recordings", headers={"X-CSRF-Token": "csrf-operator", "Origin": origin}
    )
    assert response.status == 200
    expect(page.locator("#recordings-list .event-card")).to_have_count(2, timeout=12000)
    expect(page.locator("#recordings-list")).to_contain_text("KM7GHS late check in")
    expect(page.locator("#checkins-list")).to_contain_text("No operator-confirmed check-ins")
    page.locator("#recordings-list .event-card").first.get_by_role(
        "button", name="Mark audio position"
    ).click()
    page.locator("#marker-form [name=note]").fill("Opening announcements")
    page.locator("#marker-form [name=audio_offset]").fill("2")
    page.locator("#marker-form [name=at]").fill("2026-09-05T12:00:02")
    page.locator("#save-marker").click()
    expect(page.locator("#markers-list")).to_contain_text("Opening announcements")
    page.locator("#detected-list .event-card").filter(has=page.get_by_role("link", name="KM7GHS", exact=True)).get_by_role("button", name="Confirm Check-In").click()
    page.locator("#checkin-form [name=note]").fill("Confirmed by ear")
    page.locator("#save-checkin").click()
    expect(page.locator("#checkins-list")).to_contain_text("KM7GHS")
    expect(page.locator("#checkins-list")).to_contain_text("Confirmed by ear")
    page.goto(origin + "/")
    expect(page.locator(".event-related")).to_contain_text("ACTIVE · Groovy Late Shift")
    page.locator(".event-related").get_by_role("button", name="End Event").click()
    expect(page.locator(".event-related")).not_to_contain_text("ACTIVE · Groovy Late Shift")
    page.goto(event_url)
    expect(page.locator("#reopen-event")).to_be_visible()
    page.locator("#reopen-event").click()
    expect(page.locator("#end-event")).to_be_visible()
    expect(page.locator("#markers-list")).to_contain_text("Opening announcements")
    expect(page.locator("#checkins-list")).to_contain_text("KM7GHS")
    page.locator("#markers-list a").first.click()
    expect(page.locator("#recording-detail")).to_be_visible()
    page.wait_for_function('() => document.querySelector("audio")?.currentTime >= 2')
    assert ids["KM7GHS"] in page.url
    page.goto(event_url)
    page.locator("#end-event").click()
    expect(page.locator("#reopen-event")).to_be_visible()


def test_archive_range_boundaries_exclusions_and_mobile_missing_audio(page, application):
    origin, ids = application
    page.goto(origin + "/archive")
    page.locator("#source_id").select_option(index=1)
    page.locator("#from").fill("2026-09-04T00:00")
    page.locator("#to").fill("2026-09-05T12:03")
    page.locator("#event-from-range").click()
    expect(page.locator("#event-editor")).to_be_visible()
    page.locator("#event-form [name=name]").fill("Yesterday archive net")
    page.locator("#preview-event").click()
    expect(page.locator("#membership-preview")).to_contain_text("automatic match")
    page.locator("#save-event").click()
    expect(page.locator("#recordings-list .event-card")).to_have_count(3)
    event_url = page.url
    page.locator("#edit-event").click()
    page.locator("#event-form [name=ended_at]").fill("2026-09-05T12:00:10")
    page.locator("#save-event").click()
    expect(page.locator("#recordings-list .event-card")).to_have_count(2)
    sample = page.locator(f'[data-recording-id="{ids["KM7GHS"]}"]')
    expect(sample).to_contain_text("Crosses event boundary")
    sample.get_by_role("button", name="Exclude", exact=True).click()
    expect(page.locator("#recordings-list .event-card")).to_have_count(1)
    page.reload()
    expect(page.locator("#recordings-list")).to_contain_text("Audio unavailable")
    page.locator("#membership-view").select_option("decisions")
    expect(page.locator("#recordings-list")).to_contain_text("EXCLUDED")
    page.locator("#membership-view").select_option("included")
    page.locator("#checkin-form [name=callsign]").fill("W1AW")
    page.locator("#checkin-form [name=at]").fill("2026-09-04T12:00")
    page.locator("#save-checkin").click()
    expect(page.locator("#checkins-list")).to_contain_text("W1AW")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path="/tmp/repeater-scribe-events-mobile.png", full_page=True)
    page.locator("#recordings-list a").first.click()
    expect(page.locator("#audio-content")).to_contain_text("Audio is no longer available")
    page.goto(event_url)
    expect(page.locator("#checkins-list")).to_contain_text("W1AW")
    expect(page.locator("#recordings-list")).to_contain_text("Legacy full text")


def test_archive_selected_preview_keeps_explicit_inclusions(page, application):
    origin, ids = application
    page.goto(origin + "/archive")
    expect(page.locator("#recordings tr").first).to_be_visible()
    page.locator(f'#recordings [data-select-recording="{ids["KM7GHS"]}"]').check()
    page.locator("#event-from-selection").click()
    expect(page.locator("#selection-note")).to_contain_text("1 selected recording")
    expect(page.locator("#membership-preview")).to_contain_text("1 explicit inclusion")
    page.locator("#event-form [name=name]").fill("Selected archive traffic")
    page.locator("#preview-event").click()
    page.locator("#save-event").click()
    expect(page.locator("#recordings-list")).to_contain_text("include")
    page.locator("#edit-event").click()
    page.locator("#event-form [name=started_at]").fill("2026-09-05T12:02")
    page.locator("#event-form [name=ended_at]").fill("2026-09-05T12:03")
    page.locator("#save-event").click()
    expect(page.locator("#recordings-list")).to_contain_text("sample.wav")
    expect(page.locator("#recordings-list")).to_contain_text("include")


def test_viewer_has_read_access_without_event_mutation_controls(page, application):
    origin, _ = application
    page.context.clear_cookies()
    page.context.add_cookies(
        [
            {
                "name": "__Host-aslt_session",
                "value": "browser-viewer",
                "url": origin,
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        ]
    )
    page.goto(origin + "/events")
    expect(page.locator("#start-event")).to_be_hidden()
    expect(page.locator("#events-list .event-card").first).to_be_visible()
    page.locator("#events-list .event-card a").first.click()
    expect(page.locator("#event-detail")).to_be_visible()
    expect(page.locator("#marker-form")).to_be_hidden()
    expect(page.locator("#checkin-form")).to_be_hidden()
    assert (
        page.request.post(
            origin + "/api/v1/sessions/start", data={}, headers={"Idempotency-Key": str(uuid4())}
        ).status
        == 403
    )
    Path("/tmp/repeater-scribe-events-desktop.png").parent.mkdir(exist_ok=True)
    page.screenshot(path="/tmp/repeater-scribe-events-desktop.png", full_page=True)
