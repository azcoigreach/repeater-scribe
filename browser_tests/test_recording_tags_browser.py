"""Recording tag drafts survive live card updates and explicit refreshes."""

from uuid import uuid4

from playwright.sync_api import expect


def open_recording(page, application):
    origin, ids = application
    source = page.request.get(origin + '/api/v1/sessions/sources').json()['items'][0]['id']
    response = page.request.post(origin + '/ui/sessions', headers={
        'X-CSRF-Token': 'csrf-operator', 'Origin': origin, 'Idempotency-Key': str(uuid4()),
    }, data={
        'name': 'Recording tag edits', 'source_id': source,
        'started_at': '2026-09-05T11:59:00Z', 'ended_at': '2026-09-05T12:03:00Z',
    })
    assert response.status == 200
    event_id = response.json()['id']
    page.clock.install()
    page.goto(origin + '/events/' + event_id)
    card = page.locator(f'#recordings-list [data-recording-id="{ids["KM7GHS"]}"]')
    expect(card).to_be_visible()
    revision = [0]

    def refreshed_recording(route):
        response = route.fetch()
        body = response.json()
        revision[0] += 1
        for row in body['items']:
            if row['id'] == ids['KM7GHS']:
                row['transcript'] = {'display_text': f'Updated transcript {revision[0]}', 'segments': []}
        route.fulfill(response=response, json=body)

    page.route(f'**/api/v1/sessions/{event_id}/recordings?*', refreshed_recording)
    return card, event_id


def test_tag_draft_focus_and_selection_survive_polling_and_refresh(page, application):
    card, event_id = open_recording(page, application)
    field = card.locator('.tag-form input')
    field.fill('funny, conversation')
    field.evaluate('(input) => input.setSelectionRange(2, 6)')
    page.clock.fast_forward(5000)
    expect(card).to_contain_text('Updated transcript 1')
    expect(field).to_have_value('funny, conversation')
    expect(field).to_be_focused()
    assert field.evaluate('(input) => [input.selectionStart, input.selectionEnd]') == [2, 6]
    card.locator('h3').click()
    page.clock.fast_forward(5000)
    expect(card).to_contain_text('Updated transcript 2')
    expect(field).to_have_value('funny, conversation')
    page.locator('#refresh-recordings').click()
    expect(card).to_contain_text('Updated transcript 3')
    expect(field).to_have_value('funny, conversation')
    card.get_by_role('button', name='Save recording tags').click()
    expect(card.get_by_role('button', name='Tags saved')).to_be_visible()
    origin, ids = application
    saved = page.request.get(origin + f'/api/v1/sessions/{event_id}/recordings').json()
    assert next(row for row in saved['items'] if row['id'] == ids['KM7GHS'])['tags'] == ['conversation', 'funny']
    page.reload()
    expect(field).to_have_value('conversation, funny')
    assert card.locator('.tag-form').evaluate('(form) => form.getBoundingClientRect().top - form.previousElementSibling.getBoundingClientRect().bottom') >= 12


def test_tag_save_preserves_new_typing_and_failed_draft_across_polls(page, application):
    card, event_id = open_recording(page, application)
    origin, ids = application
    field = card.locator('.tag-form input')
    save = card.locator('.tag-form button')
    pending = []
    patch_url = origin + f'/ui/sessions/{event_id}/recordings/{ids["KM7GHS"]}/tags'
    page.route(patch_url, lambda route: pending.append(route))
    field.fill('original')
    with page.expect_request(patch_url):
        save.click()
    expect(save).to_be_disabled()
    field.fill('new draft')
    page.clock.fast_forward(5000)
    expect(card).to_contain_text('Updated transcript 1')
    expect(field).to_have_value('new draft')
    expect(field).to_be_focused()
    pending.pop().continue_()
    expect(save).to_be_enabled()
    expect(save).to_have_text('Save recording tags')
    expect(field).to_have_value('new draft')
    page.clock.fast_forward(5000)
    expect(card).to_contain_text('Updated transcript 2')
    expect(field).to_have_value('new draft')
    with page.expect_request(patch_url):
        save.click()
    pending.pop().fulfill(status=503, json={'detail': 'Temporary tag failure'})
    expect(save).to_have_text('Retry saving tags')
    expect(page.locator('#event-error')).to_contain_text('Temporary tag failure')
    page.clock.fast_forward(5000)
    expect(card).to_contain_text('Updated transcript 3')
    expect(field).to_have_value('new draft')
    with page.expect_request(patch_url):
        save.click()
    pending.pop().continue_()
    expect(save).to_have_text('Tags saved')
    page.reload()
    expect(field).to_have_value('new draft')
