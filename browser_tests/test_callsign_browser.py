from playwright.sync_api import expect

HOSTILE = '<img src=x onerror="window.hostile=true"> & <script>window.hostile=true</script>'


def open_profile(page, application, callsign="KM7GHS"):
    page.goto(application[0] + "/callsigns/" + callsign)
    expect(page.locator("#history .recording").first).to_be_visible()


def test_directory_search_sort_pagination_and_navigation(page, application):
    origin, _ = application
    page.goto(origin + "/")
    page.get_by_role("link", name="Callsigns", exact=True).click()
    expect(page.locator("#callsign-directory .recording")).to_have_count(50)
    expect(page.locator("#callsign-directory")).to_contain_text("First heard:")
    expect(page.locator("#callsign-directory")).to_contain_text("Most recent confidence:")
    page.locator("#callsign-more").click()
    expect(page.locator("#callsign-directory .recording")).to_have_count(56)
    names = page.locator("#callsign-directory h3").all_text_contents()
    assert len(names) == len(set(names))
    expect(page.locator("#callsign-more")).to_be_hidden()
    page.locator("#callsign-sort").select_option("alphabetical")
    page.locator("#callsign-search button").click()
    expect(page.locator("#callsign-directory .recording")).to_have_count(50)
    names = page.locator("#callsign-directory h3").all_text_contents()
    assert names == sorted(names)
    page.locator("#callsign-query").fill("K1")
    page.locator("#callsign-search button").click()
    expect(page.locator("#callsign-directory .recording")).to_have_count(50)
    assert all(
        name.startswith("K1") for name in page.locator("#callsign-directory h3").all_text_contents()
    )
    page.locator("#callsign-query").fill("KM7GHS")
    page.locator("#callsign-search button").click()
    expect(page).to_have_url(origin + "/callsigns/KM7GHS")
    expect(page.locator("#profile-name")).to_contain_text("Operator")
    page.get_by_role("link", name="Archive", exact=True).click()
    expect(page.locator("#recordings tr").first).to_be_visible()
    page.locator("#recordings .callsign-evidence").first.click()
    expect(page).to_have_url(origin + "/callsigns/KM7GHS")
    page.get_by_role("link", name="Open recording", exact=True).first.click()
    expect(page.locator("#recording-detail")).to_be_visible()
    page.locator(".callsign-history-link").first.click()
    expect(page).to_have_url(origin + "/callsigns/KM7GHS")


def test_history_filters_pagination_evidence_and_audio_seeking(page, application):
    open_profile(page, application)
    expect(page.locator("#history .recording")).to_have_count(50)
    page.locator("#history-more").click()
    expect(page.locator("#history .recording")).to_have_count(55)
    expect(page.locator("#history-more")).to_be_hidden()
    # Reviews identify unique rows; card count alone would not detect duplicate pages.
    ids = page.locator("#history .recording").evaluate_all(
        "(cards) => cards.map(card => card.dataset.mentionId)"
    )
    assert len(set(ids)) == 55
    expect(page.locator("#history")).to_contain_text("Raw observed: K M 7 G H S")
    expect(page.locator("#history")).to_contain_text("Recognition method: phonetic")
    expect(page.locator("#history")).to_contain_text("Whisper segment avg_logprob (raw): -0.25")
    expect(page.locator("#history")).to_contain_text("segment timing")
    expect(page.locator("#history")).to_contain_text("Saved evidence " + HOSTILE)
    page.get_by_role("button", name="Play from mention", exact=True).first.click()
    page.wait_for_function(
        "() => player.currentTime >= 2 && player.currentTime < 5 && !player.paused"
    )
    page.evaluate("player.pause()")
    page.locator("[name=audio_status]").select_option("missing")
    page.locator("#history-filters button").click()
    expect(page.locator("#history .recording")).to_have_count(0)
    page.locator("[name=audio_status]").select_option("")
    page.locator("[name=from]").fill("2026-09-06")
    page.locator("#history-filters button").click()
    expect(page.locator("#history-state")).to_have_text("No mention history matches these filters.")
    page.locator("[name=from]").fill("2026-09-05")
    page.locator("[name=to]").fill("2026-09-05")
    page.locator("#history-filters button").click()
    expect(page.locator("#history .recording")).to_have_count(50)


def test_operator_confirmation_rejection_correction_and_refresh(page, application):
    open_profile(page, application)
    page.get_by_role("button", name="Confirm", exact=True).first.click()
    page.locator("[name=review_status]").select_option("confirmed")
    page.locator("#history-filters button").click()
    expect(page.locator("#history .recording")).to_have_count(1)
    page.get_by_role("button", name="Reject", exact=True).click()
    expect(page.locator("#history .recording")).to_have_count(0)
    page.locator("[name=review_status]").select_option("rejected")
    page.locator("#history-filters button").click()
    expect(page.locator("#history .recording")).to_have_count(1)
    page.once("dialog", lambda dialog: dialog.accept("KE7WIL"))
    page.get_by_role("button", name="Correct", exact=True).click()
    expect(page.locator("#history .recording")).to_have_count(0)
    open_profile(page, application, "KE7WIL")
    expect(page.locator("#history")).to_contain_text("corrected")
    expect(page.locator("#profile")).to_contain_text("Explicitly attributed transmissions: 0")
    page.get_by_role("button", name="Refresh QRZ", exact=True).click()
    expect(page.locator("#profile-name")).to_contain_text("Refreshed")


def test_viewer_controls_missing_audio_unknown_confidence_and_legacy_text(page, application):
    origin, ids = application
    page.context.clear_cookies()
    page.context.add_cookies(
        [{"name": "__Host-aslt_session", "value": "browser-viewer", "url": origin, "secure": True}]
    )
    open_profile(page, application, "K1AB")
    for label in ("Confirm", "Reject", "Correct", "Refresh QRZ"):
        expect(page.get_by_role("button", name=label, exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Play from mention")).to_be_disabled()
    expect(page.locator("#history")).to_contain_text("Audio missing")
    expect(page.locator("#history")).to_contain_text("Saved evidence")
    expect(page.locator("#profile")).to_contain_text("average Unavailable")
    expect(page.locator("#history")).to_contain_text("Overall confidence: Unavailable")
    page.get_by_role("link", name="Open recording", exact=True).click()
    expect(page).to_have_url(origin + "/archive/recordings/" + ids["K1AB"])
    expect(page.locator("#transcript-content")).to_contain_text("Legacy full text K1AB")
    expect(page.locator(".transcript-segment")).to_have_count(0)
    expect(page.locator("#audio-content")).to_contain_text("Audio is no longer available")
    expect(page.locator("#callsign-evidence")).to_contain_text("Saved evidence")


def test_normalized_segments_seek_and_literal_hostile_content(page, application):
    origin, ids = application
    page.goto(origin + "/archive/recordings/" + ids["KM7GHS"])
    expect(page.locator(".transcript-segment")).to_have_count(1)
    expect(page.locator(".transcript-segment")).to_contain_text(HOSTILE)
    expect(page.locator(".transcript-segment")).to_contain_text("avg_logprob (raw): -0.25")
    page.locator(".segment-seek").click()
    page.wait_for_function(
        '() => document.querySelector("audio").currentTime >= 2 && document.querySelector("audio").currentTime < 5'
    )
    page.evaluate('document.querySelector("audio").pause()')
    page.locator("#callsign-evidence button").first.click()
    page.wait_for_function(
        '() => document.querySelector("audio").currentTime >= 2 && !document.querySelector("audio").paused'
    )
    assert (
        page.locator(
            "#transcript-content img, #callsign-evidence img, #transcript-content script"
        ).count()
        == 0
    )
    assert page.evaluate("window.hostile") is None
    open_profile(page, application)
    expect(page.locator("#profile-name")).to_contain_text(HOSTILE)
    assert (
        page.locator(
            '#profile img, #profile a[href^="data:"], #profile a[href^="javascript:"]'
        ).count()
        == 0
    )


def test_dashboard_polling_provisional_final_callsign_audio_and_hostile_dom(page, application):
    origin, _ = application
    page.goto(origin + "/")
    page.wait_for_function(
        '() => typeof renderJobs === "function" && typeof loadJobs === "function"'
    )
    # Exercise the same renderer used by polling and SSE with transient live output.
    item = {
        "source_path": "sample.wav",
        "audio_url": origin + "/api/v1/audio?path=sample.wav",
        "status": "live",
        "callsigns": ["KM7GHS"],
        "transcript": {"display_text": "KM7GHS " + HOSTILE, "provisional": True},
    }
    page.route(
        "**/api/v1/recordings?*", lambda route: route.fulfill(json={"items": [item], "total": 1})
    )
    page.evaluate("async () => { await loadCallsigns(); await loadJobs(); }")
    expect(page.locator("#recordings")).to_contain_text("(provisional)")
    expect(page.locator("#recordings")).to_contain_text(HOSTILE)
    assert page.locator("#recordings img, #recordings script").count() == 0
    # Actual polling still updates the renderer after a provisional response.
    page.unroute("**/api/v1/recordings?*")
    page.evaluate("loadJobs()")
    expect(page.locator("#recordings")).not_to_contain_text("(provisional)")
    page.wait_for_function('() => document.querySelectorAll("#recordings .recording").length > 0')
    page.locator("#recordings .play-button").first.click()
    page.wait_for_function("() => !player.paused")
    page.evaluate("player.pause()")
    page.evaluate("activatePanel('callsigns')")
    expect(page.locator(".callsign-card").first).to_be_visible()
    expect(page.locator("#last-heard-callsigns")).to_contain_text(HOSTILE)
    assert page.locator("#last-heard-callsigns img").count() == 0
    assert page.evaluate("window.hostile") is None
    page.locator(".callsign-card a.callsign-evidence").first.click()
    expect(page).to_have_url(origin + "/callsigns/KM7GHS")
    page.goto(origin + "/")
    page.evaluate("activatePanel('transcripts')")
    page.locator("#recordings .transcript-callsign").first.click()
    assert "/callsigns/" in page.url


def test_dashboard_favorites_topology_literal_text_and_controls(page, application):
    page.route(
        "**/api/v1/nodes/*/favorites",
        lambda route: route.fulfill(
            json={
                "items": [
                    {
                        "id": "fixture",
                        "target_identifier": "200",
                        "callsign": HOSTILE,
                        "label": "Fixture",
                        "location": HOSTILE,
                    }
                ]
            }
        ),
    )
    page.goto(application[0] + "/")
    page.wait_for_function('() => typeof renderFavorites === "function"')
    page.evaluate(
        """(hostile) => {
      favoriteItems = [{id: 'fixture', target_identifier: '200', callsign: hostile, label: 'Fixture', location: hostile}];
      currentConnections = [{identifier: '200', callsign: hostile, connection_state: 'established', keyed: true}];
      renderConnectedStations(currentConnections); renderFavorites(); topologyRootFavoriteId = 'fixture';
      renderTopology(); activatePanel('favorites');
    }""",
        HOSTILE,
    )
    expect(page.locator("#favorites")).to_contain_text(HOSTILE)
    assert page.locator("#favorites img, #stations img").count() == 0
    page.locator(".favorite-topology").click()
    expect(page.locator("#topology-chart svg")).to_be_visible()
    assert page.locator("#topology-chart img, #topology-chart script").count() == 0
    page.locator("#topology-chart .topology-node").first.click()
    expect(page.locator("#topology-details")).to_contain_text(HOSTILE)
    expect(page.locator(".topology-edit-favorite")).to_be_visible()


def test_desktop_and_mobile_layouts_keep_controls_in_view(page, application):
    origin, ids = application
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        for path, ready in [
            ("/", ".workspace-nav"),
            ("/archive", "#recordings tr"),
            ("/callsigns", "#callsign-directory .recording"),
            ("/callsigns/KM7GHS", "#history .recording"),
            ("/archive/recordings/" + ids["KM7GHS"], "#recording-detail"),
        ]:
            page.goto(origin + path)
            page.locator(ready).first.wait_for(state="attached")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                width,
                path,
            )
            clipped = page.locator(
                "button:visible, input:visible, select:visible, .workspace-link:visible"
            ).evaluate_all(
                "(nodes) => nodes.filter(node => { const r = node.getBoundingClientRect(); return r.left < -1 || r.right > innerWidth + 1; }).map(node => node.outerHTML)"
            )
            assert clipped == [], (width, path, clipped)


def test_concurrent_load_more_does_not_duplicate_rows(page, application):
    page.goto(application[0] + "/callsigns")
    expect(page.locator("#callsign-directory .recording")).to_have_count(50)
    page.evaluate("Promise.all([load(), load()])")
    # Previous review scenario creates KE7WIL, so derive expected membership from the API.
    expected = page.request.get(application[0] + "/api/v1/callsigns?limit=100").json()["items"]
    expect(page.locator("#callsign-directory .recording")).to_have_count(len(expected))
    names = page.locator("#callsign-directory h3").all_text_contents()
    assert len(names) == len(set(names))
    open_profile(page, application)
    expect(page.locator("#history .recording")).to_have_count(50)
    page.evaluate("Promise.all([loadHistory(), loadHistory()])")
    expected = page.request.get(
        application[0] + "/api/v1/callsigns/KM7GHS/mentions?limit=100"
    ).json()["items"]
    expect(page.locator("#history .recording")).to_have_count(len(expected))
    identifiers = page.locator("#history .recording").evaluate_all(
        "(cards) => cards.map(card => card.dataset.mentionId)"
    )
    assert len(identifiers) == len(set(identifiers))
