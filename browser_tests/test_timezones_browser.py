"""Real SQLite/API/browser timezone boundaries; no production data is used."""

from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize('page,hour', [('America/Phoenix', '5'), ('UTC', '12'), ('America/New_York', '8')], indirect=['page'])
def test_display_and_calendar_roundtrip_across_workspaces(page, hour, application):
    origin, ids = application
    query = urlencode({'from': '2026-09-05T12:00:00.000Z', 'to': '2026-09-05T12:00:12.000Z'})
    page.goto(origin + '/archive?' + query)
    expect(page.locator('#recordings tr')).to_have_count(1)
    stamp = f'Sep 5, 2026, {hour}:00:00'
    expect(page.locator('#recordings time')).to_contain_text(stamp)
    expect(page.locator('.timezone-help').first).to_contain_text(page.evaluate('UITime.zone'))
    expect(page.locator('#from')).to_have_value(f'2026-09-05T{int(hour):02}:00')
    page.locator('#archive-filters button[type=submit]').click()
    assert parse_qs(urlparse(page.url).query)['from'] == ['2026-09-05T12:00:00.000Z']
    page.reload()
    expect(page.locator('#from')).to_have_value(f'2026-09-05T{int(hour):02}:00')
    page.locator('#source_id').select_option(index=1)
    page.locator('#event-from-range').click()
    expect(page.locator('#event-editor')).to_be_visible()
    expect(page.locator('#event-form [name=started_at]')).to_have_value(f'2026-09-05T{int(hour):02}:00')
    page.locator('#event-form [name=name]').fill('Timezone roundtrip')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/ui/sessions')) as created:
        page.locator('#save-event').click()
    assert created.value.status == 200
    page.wait_for_url('**/events/*')
    event = page.request.get(origin + '/api/v1/sessions/' + page.url.rsplit('/', 1)[1]).json()
    assert event['started_at'] in ['2026-09-05T12:00:00Z', '2026-09-05T12:00:00+00:00']
    expect(page.locator('#recordings-list')).to_contain_text(stamp)
    page.locator('#edit-event').click()
    expect(page.locator('#event-form [name=started_at]')).to_have_value(f'2026-09-05T{int(hour):02}:00')
    page.locator('#save-event').click()
    expect(page.locator('#event-editor')).to_be_hidden()
    page.locator('#recordings-list').get_by_role('button', name='Mark audio position').click()
    expect(page.locator('#marker-form [name=at]')).to_have_value(f'2026-09-05T{int(hour):02}:00')
    page.locator('#marker-form [name=note]').fill('Local time marker')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/markers')) as marker:
        page.locator('#save-marker').click()
    assert marker.value.json()['at'].startswith('2026-09-05T12:00:00')
    page.locator('#markers-list').get_by_role('button', name='Edit marker').click()
    expect(page.locator('#marker-form [name=at]')).to_have_value(f'2026-09-05T{int(hour):02}:00')
    page.locator('#detected-list .event-card').filter(has=page.get_by_role('link', name='KM7GHS', exact=True)).get_by_role('button', name='Confirm Check-In').click()
    expect(page.locator('#checkin-form [name=at]')).to_have_value(f'2026-09-05T{int(hour):02}:00:04')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/checkins')) as checkin:
        page.locator('#save-checkin').click()
    assert checkin.value.json()['at'].startswith('2026-09-05T12:00:04')
    page.goto(origin + '/archive/recordings/' + ids['KM7GHS'])
    expect(page.locator('#recording-time')).to_contain_text(stamp)
    page.goto(origin + '/callsigns')
    station = page.locator('#callsign-directory article').filter(has=page.get_by_role('link', name='KM7GHS', exact=True))
    expect(station).to_contain_text(f'{hour}:00:04')
    page.goto(origin + '/callsigns/KM7GHS')
    expect(page.locator('#profile')).to_contain_text(f'{hour}:00:04')
    expect(page.locator('#history article').filter(has=page.locator(f'a[href="/archive/recordings/{ids["KM7GHS"]}"]')).first.locator('h3')).to_contain_text(f'{hour}:00:04')
    page.locator('#history-filters [name=from]').fill('2026-09-05')
    page.locator('#history-filters [name=to]').fill('2026-09-05')
    with page.expect_request(lambda r: '/mentions?' in r.url) as request:
        page.locator('#history-filters button[type=submit]').click()
    bounds = parse_qs(urlparse(request.value.url).query)
    offset = 12 - int(hour)
    assert bounds['from'] == [f'2026-09-05T{offset:02}:00:00.000Z']
    end = f'2026-09-06T{offset - 1:02}:59:59.999999Z' if offset else '2026-09-05T23:59:59.999999Z'
    assert bounds['to'] == [end]
    expect(page.locator('#history article')).to_have_count(50)
    page.goto(origin + '/')
    expect(page.locator('#last-heard-callsigns')).to_contain_text(f'{hour}:00:04')


@pytest.mark.parametrize('page', ['America/Phoenix', 'America/New_York', 'Asia/Kolkata'], indirect=True)
def test_time_helpers_preserve_offsets_precision_and_local_days(page, application):
    origin, _ = application
    page.goto(origin + '/archive')
    result = page.evaluate('''() => {
      const values = ['2026-09-05T23:59:59.123Z', '2026-01-01T00:00:00Z', '2026-03-08T06:30:00Z'];
      return values.map(value => UITime.toUTC(UITime.localInput(value)));
    }''')
    assert result == ['2026-09-05T23:59:59.123Z', '2026-01-01T00:00:00.000Z', '2026-03-08T06:30:00.000Z']
    assert page.evaluate("UITime.format('2026-09-05T12:00:00') === UITime.format('2026-09-05T12:00:00Z')")
    assert page.evaluate("UITime.format('not-a-time')") == 'Time unavailable'
    if page.evaluate('UITime.zone') == 'America/New_York':
        assert page.evaluate("UITime.toUTC('2026-03-08')") == '2026-03-08T05:00:00.000Z'
        assert page.evaluate("UITime.toUTC('2026-03-08', true)") == '2026-03-09T03:59:59.999999Z'
        assert page.evaluate("UITime.toUTC('2026-11-01', true)") == '2026-11-02T04:59:59.999999Z'
        page.locator('#from').fill('2026-03-08T02:30')
        page.locator('#archive-filters button[type=submit]').click()
        expect(page.locator('#filter-error')).to_contain_text('does not exist')


@pytest.mark.parametrize('page', ['America/Phoenix'], indirect=True)
def test_archive_legacy_bookmarks_and_event_filter_urls(page, application):
    origin, _ = application
    page.goto(origin + '/archive?from=2026-09-05&to=2026-09-05')
    expect(page.locator('#from')).to_have_value('2026-09-05T00:00')
    expect(page.locator('#to')).to_have_value('2026-09-05T23:59:59.999')
    page.goto(origin + '/events?from=2026-09-05T12:00:00Z&to=2026-09-05T13:00:00Z')
    expect(page.locator('#event-filters [name=from]')).to_have_value('2026-09-05T05:00')
    expect(page.locator('#event-filters [name=to]')).to_have_value('2026-09-05T06:00')


@pytest.mark.parametrize('page', ['America/New_York'], indirect=True)
def test_unchanged_calendars_preserve_repeated_hour_and_microseconds(page, application):
    origin, _ = application
    # The second 01:30 on fall-back day must remain 06:30 UTC after reload/save.
    query = urlencode({'from': '2025-11-02T06:30:00.123456Z', 'to': '2025-11-02T07:30:00Z'})
    page.goto(origin + '/archive?' + query)
    expect(page.locator('#from')).to_have_value('2025-11-02T01:30:00.123')
    page.locator('#source_id').select_option(index=1)
    page.locator('#archive-filters button[type=submit]').click()
    assert parse_qs(urlparse(page.url).query)['from'] == ['2025-11-02T06:30:00.123456Z']
    page.reload()
    page.locator('#event-from-range').click()
    expect(page.locator('#event-form [name=started_at]')).to_have_value('2025-11-02T01:30:00.123')
    page.locator('#event-form [name=name]').fill('Repeated hour')
    with page.expect_request(lambda r: r.method == 'POST' and r.url.endswith('/ui/sessions')) as request:
        page.locator('#save-event').click()
    assert request.value.post_data_json['started_at'] == '2025-11-02T06:30:00.123456Z'
    page.wait_for_url('**/events/*')
    page.locator('#edit-event').click()
    expect(page.locator('#event-form [name=started_at]')).to_have_value('2025-11-02T01:30:00.123')
    with page.expect_request(lambda r: r.method == 'PATCH' and not r.url.endswith('/tags')) as request:
        page.locator('#save-event').click()
    assert request.value.post_data_json['started_at'] in [
        '2025-11-02T06:30:00.123456Z', '2025-11-02T06:30:00.123456+00:00',
    ]
