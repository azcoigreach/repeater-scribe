from playwright.sync_api import expect

PREVIEW = 'The complete original transmission remains visible while transcription runs.'


def test_log_preserves_text_and_exposes_retry_progress_and_errors(page, application):
    origin, _ = application
    item = {
        'id': 'retry-job', 'source_path': 'call.wav', 'status': 'failed',
        'audio_url': '/api/v1/audio?path=call.wav', 'callsigns': [],
        'transcript': {'display_text': PREVIEW, 'provisional': True},
        'last_error': 'Final pass lost text. Previous text retained.',
    }
    requests = []
    page.route('**/api/v1/recordings?*', lambda route: route.fulfill(json={'items': [item]}))

    def retry(route):
        requests.append(route.request)
        item['status'] = 'pending'
        item['last_error'] = None
        route.fulfill(status=202, json={'id': item['id'], 'status': 'pending'})

    page.route('**/ui/ingestion/jobs/retry-job/retry', retry)
    page.goto(origin + '/')
    page.evaluate("activatePanel('transcripts')")
    log = page.locator('#recordings')
    expect(log).to_contain_text(PREVIEW)
    expect(log).to_contain_text('Final pass lost text')
    button = log.get_by_role('button', name='Re-transcribe', exact=True)
    button.click()
    expect(button).to_be_disabled()
    expect(log).to_contain_text(PREVIEW)
    assert requests[0].method == 'POST'
    assert requests[0].headers['x-csrf-token']
    item['status'] = 'completed'
    item['transcript'] = {'display_text': PREVIEW + ' Recovered ending.', 'provisional': False}
    page.evaluate('loadJobs()')
    expect(log).to_contain_text('Recovered ending.')
    expect(log).not_to_contain_text('(provisional)')
    expect(button).to_be_enabled()


def test_detail_retry_polls_result_preserves_audio_and_handles_errors(page, application):
    origin, ids = application
    identifier = ids['KM7GHS']
    path = '/api/v1/archive/recordings/' + identifier
    item = page.request.get(origin + path).json()
    page.route('**' + path, lambda route: route.fulfill(json=item))
    fail = False

    def retry(route):
        if fail:
            route.fulfill(status=409, json={'detail': 'Recording audio is unavailable'})
        else:
            item['status'] = 'pending'
            route.fulfill(status=202, json={'id': identifier, 'status': 'pending'})

    page.route('**/ui/ingestion/jobs/' + identifier + '/retry', retry)
    page.goto(origin + '/archive/recordings/' + identifier)
    button = page.get_by_role('button', name='Re-transcribe', exact=True)
    expect(button).to_be_enabled()
    original = item['transcript']['display_text']
    page.evaluate("document.querySelector('audio').dataset.original = 'true'")
    button.click()
    expect(button).to_be_disabled()
    expect(page.locator('#retry-status')).to_contain_text('in progress')
    expect(page.locator('#transcript-content')).to_contain_text(original)
    item['status'] = 'completed'
    item['transcript']['display_text'] = PREVIEW
    expect(page.locator('#transcript-content')).to_contain_text(PREVIEW)
    expect(button).to_be_enabled()
    assert page.locator('audio').get_attribute('data-original') == 'true'
    fail = True
    button.click()
    expect(page.locator('#retry-status')).to_have_text('Recording audio is unavailable')
    expect(button).to_be_enabled()
    expect(page.locator('#transcript-content')).to_contain_text(PREVIEW)
