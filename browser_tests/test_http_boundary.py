"""Real authenticated HTTP, range and SSE requests, optionally through reference Caddy."""

import time
from uuid import uuid4

import httpx
from playwright.sync_api import expect


def test_repeated_real_event_and_favorites_reads(page, application):
    origin, _ = application
    source = page.request.get(origin + "/api/v1/sessions/sources").json()["items"][0]["id"]
    response = page.request.post(origin + "/ui/sessions", headers={
        "Origin": origin, "X-CSRF-Token": "csrf-operator", "Idempotency-Key": str(uuid4()),
    }, data={
        "name": "Proxy polling fixture", "source_id": source,
        "started_at": "2026-09-05T12:00:00Z", "ended_at": "2026-09-05T12:01:00Z",
    })
    assert response.status == 200
    event_id = response.json()["id"]
    page.goto(origin + f"/events/{event_id}")
    expect(page.locator("#event-title")).to_have_text("Proxy polling fixture")
    paths = [f"/api/v1/sessions/{event_id}"] + [
        f"/api/v1/sessions/{event_id}/{kind}?limit=25"
        for kind in ("markers", "recordings", "checkins", "detected")
    ] + ["/api/v1/nodes/100/favorites"]
    statuses = page.evaluate("""async paths => {
        const statuses = [];
        for (let round = 0; round < 6; round++) {
            if (round) await new Promise(resolve => setTimeout(resolve, 5000));
            statuses.push(...await Promise.all(paths.map(async path => {
                const response = await fetch(path, {cache: 'no-store'});
                await response.json();
                return response.status;
            })));
        }
        return statuses;
    }""", paths)
    assert statuses == [200] * 36
    expect(page.locator("#event-error")).to_be_hidden()


def test_read_authorization_csrf_origin_and_audio_range(application):
    origin, ids = application
    audio = f"/api/v1/archive/recordings/{ids['KM7GHS']}/audio"
    with httpx.Client(base_url=origin, verify=False, trust_env=False) as client:
        assert client.get(audio).status_code == 401
        client.cookies.set("__Host-aslt_session", "browser-viewer")
        response = client.get(audio, headers={"Range": "bytes=0-15"})
        assert response.status_code == 206
        assert response.headers["content-range"].startswith("bytes 0-15/")
        assert len(response.content) == 16
        assert response.content.startswith(b"RIFF")
        endpoint = "/ui/nodes/100/favorites"
        body = {"target_identifier": "200", "label": "Boundary fixture"}
        assert client.post(endpoint, json=body, headers={
            "Origin": origin, "X-CSRF-Token": "csrf-viewer",
        }).status_code == 403
        client.cookies.set("__Host-aslt_session", "browser-operator")
        assert client.post(endpoint, json=body, headers={"Origin": origin}).status_code == 403
        assert client.post(endpoint, json=body, headers={
            "Origin": "https://untrusted.example.test", "X-CSRF-Token": "csrf-operator",
        }).status_code == 403
        assert client.post(endpoint, json=body, headers={
            "Origin": origin, "X-CSRF-Token": "csrf-operator",
        }).status_code == 200


def test_active_sse_survives_both_idle_timeouts(application):
    origin, _ = application
    with httpx.Client(
        base_url=origin, verify=False, trust_env=False, timeout=20,
        cookies={"__Host-aslt_session": "browser-viewer"},
    ) as client:
        started = time.monotonic()
        with client.stream("GET", "/api/v1/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.iter_lines()
            assert next(lines) == "event: ready"
            assert time.monotonic() - started < 5  # No proxy buffering of the ready event.
            for line in lines:
                if line == ": heartbeat":
                    break
            else:
                raise AssertionError("SSE closed before its heartbeat")
            assert time.monotonic() - started > 5
