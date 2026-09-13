"""Whole-second Events calendars retain precise evidence until explicitly edited."""

from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def assert_instant(actual, expected):
    assert datetime.fromisoformat(actual) == datetime.fromisoformat(expected)


def create_event(page, origin, start, end):
    source = page.request.get(origin + '/api/v1/sessions/sources').json()['items'][0]['id']
    response = page.request.post(
        origin + '/ui/sessions',
        headers={'X-CSRF-Token': 'csrf-operator', 'Idempotency-Key': str(uuid4()), 'Origin': origin},
        data={'name': 'Precise calendar ' + str(uuid4()), 'source_id': source,
              'started_at': start, 'ended_at': end},
    )
    assert response.status == 200, response.text()
    return response.json()


@pytest.mark.parametrize('page,hour', [
    ('America/Phoenix', '05'), ('UTC', '12'), ('Asia/Kolkata', '17:30'),
], indirect=['page'])
def test_event_calendars_defaults_preserve_then_edit_and_clear(page, application, hour):
    origin, _ = application
    local = '2026-09-05T' + (hour if ':' in hour else hour + ':00')
    start, end = '2026-09-05T12:00:02.123456Z', '2026-09-05T12:00:10.987654Z'
    page.goto(origin + '/events')
    page.locator('#historical-event').click()
    calendars = page.locator('input[type=datetime-local]')
    assert calendars.count() == 6
    assert calendars.evaluate_all("nodes => nodes.every(n => n.step === '1' && !n.validity.stepMismatch && !n.value.includes('.'))")
    page.locator('#cancel-event').click()
    page.goto(origin + '/events?' + urlencode({'create': 'historical', 'from': start, 'to': end}))
    expect(page.locator('#event-editor')).to_be_visible()
    started = page.locator('#event-form [name=started_at]')
    ended = page.locator('#event-form [name=ended_at]')
    expect(started).to_have_value(local + ':02')
    expect(ended).to_have_value(local + ':10')
    page.locator('#event-form [name=name]').fill('Whole second calendars')
    page.locator('#save-event').click()
    page.wait_for_url('**/events/*')
    expect(page.locator('#event-title')).to_have_text('Whole second calendars')
    endpoint = origin + '/api/v1/sessions/' + page.url.rsplit('/', 1)[1]
    saved = page.request.get(endpoint).json()
    assert_instant(saved['started_at'], start)
    assert_instant(saved['ended_at'], end)
    page.locator('#edit-event').click()
    started.focus()
    page.locator('#event-form [name=description]').fill('Unrelated metadata edit')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith(saved['id'])):
        page.locator('#save-event').click()
    expect(page.locator('#event-editor')).to_be_hidden()
    saved = page.request.get(endpoint).json()
    assert_instant(saved['started_at'], start)
    assert_instant(saved['ended_at'], end)
    page.locator('#edit-event').click()
    # Returning to the initially displayed second still counts as an explicit edit.
    started.fill(local + ':03')
    started.fill(local + ':02')
    ended.fill(local + ':11')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith(saved['id'])):
        page.locator('#save-event').click()
    expect(page.locator('#event-editor')).to_be_hidden()
    saved = page.request.get(endpoint).json()
    assert_instant(saved['started_at'], '2026-09-05T12:00:02Z')
    assert_instant(saved['ended_at'], '2026-09-05T12:00:11Z')
    page.locator('#edit-event').click()
    ended.fill('')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith(saved['id'])):
        page.locator('#save-event').click()
    expect(page.locator('#event-editor')).to_be_hidden()
    assert page.request.get(endpoint).json()['ended_at'] is None
    # Leave no active event on the shared fixture source.
    page.locator('#end-event').click()
    expect(page.locator('#reopen-event')).to_be_visible()


@pytest.mark.parametrize('kind,form,list_id,edit,save', [
    ('markers', 'marker', 'markers', 'Edit marker', 'save-marker'),
    ('checkins', 'checkin', 'checkins', 'Edit check-in', 'save-checkin'),
])
@pytest.mark.parametrize('page', ['America/New_York'], indirect=True)
def test_annotation_calendars_keep_microseconds_and_fractional_audio_offsets(
    page, application, kind, form, list_id, edit, save,
):
    origin, ids = application
    event = create_event(page, origin, '2026-09-05T12:00:00Z', '2026-09-05T12:00:12Z')
    endpoint = origin + f'/ui/sessions/{event["id"]}/{kind}'
    precise = '2026-09-05T12:00:02.123456Z'
    response = page.request.post(endpoint, headers={'X-CSRF-Token': 'csrf-operator', 'Origin': origin}, data={
        'at': precise, 'note': 'Precise evidence', 'callsign': 'KM7GHS',
        'recording_id': ids['KM7GHS'], 'audio_offset': 2.125,
    })
    assert response.status == 200, response.text()
    identifier = response.json()['id']
    page.goto(origin + '/events/' + event['id'])
    page.locator('#' + list_id + '-list').get_by_role('button', name=edit).click()
    calendar = page.locator('#' + form + '-form [name=at]')
    offset = page.locator('#' + form + '-form [name=audio_offset]')
    expect(calendar).to_have_value('2026-09-05T08:00:02')
    expect(offset).to_have_value('2.125')
    expect(offset).to_have_attribute('step', '0.001')
    page.locator('#' + form + '-form [name=note]').fill('Unrelated note edit')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith(identifier)) as saved:
        page.locator('#' + save).click()
    assert saved.value.status == 200
    assert_instant(saved.value.json()['at'], precise)
    assert saved.value.json()['audio_offset'] == 2.125
    expect(page.locator('#' + list_id + '-list')).to_contain_text('Unrelated note edit')
    page.locator('#' + list_id + '-list').get_by_role('button', name=edit).click()
    calendar.fill('2026-09-05T08:00:03')
    calendar.fill('2026-09-05T08:00:02')
    with page.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith(identifier)) as saved:
        page.locator('#' + save).click()
    assert saved.value.status == 200
    assert_instant(saved.value.json()['at'], '2026-09-05T12:00:02Z')
    assert saved.value.json()['audio_offset'] == 2.125


@pytest.mark.parametrize('page', ['America/New_York'], indirect=True)
def test_event_filters_preserve_precision_exclude_to_and_reject_dst_gap(page, application):
    origin, _ = application
    start, end = '2025-11-02T06:30:02.123456Z', '2025-11-02T07:30:02.987654Z'
    included = create_event(page, origin, start, '2025-11-02T08:00:00Z')
    excluded = create_event(page, origin, end, '2025-11-02T08:00:00Z')
    page.goto(origin + '/events?' + urlencode({'from': start, 'to': end}))
    from_input = page.locator('#event-filters [name=from]')
    to_input = page.locator('#event-filters [name=to]')
    submit = page.locator('#event-filters button[type=submit], #event-filters button.primary-button')
    expect(from_input).to_have_value('2025-11-02T01:30:02')
    expect(to_input).to_have_value('2025-11-02T02:30:02')
    expect(page.locator('#events-list')).to_contain_text(included['name'])
    expect(page.locator('#events-list')).not_to_contain_text(excluded['name'])
    with page.expect_response(lambda r: '/api/v1/sessions?' in r.url) as result:
        submit.click()
    assert result.value.status == 200
    bounds = parse_qs(urlparse(result.value.url).query)
    assert bounds['from'] == [start]
    assert bounds['to'] == [end]
    from_input.fill('2025-11-02T00:30:02')
    to_input.fill('2025-11-02T02:30:03')
    with page.expect_response(lambda r: '/api/v1/sessions?' in r.url) as result:
        submit.click()
    assert result.value.status == 200
    bounds = parse_qs(urlparse(result.value.url).query)
    assert bounds['from'] == ['2025-11-02T04:30:02.000Z']
    assert bounds['to'] == ['2025-11-02T07:30:03.000Z']
    expect(page.locator('#events-list')).to_contain_text(excluded['name'])
    from_input.fill('2026-03-08T02:30:02')
    submit.click()
    expect(page.locator('#event-error')).to_contain_text('does not exist in America/New_York')
    from_input.fill('')
    to_input.fill('')
    with page.expect_response(lambda r: '/api/v1/sessions?' in r.url) as result:
        submit.click()
    assert result.value.status == 200
    assert 'from' not in parse_qs(urlparse(result.value.url).query)
    assert 'to' not in parse_qs(urlparse(result.value.url).query)
