from playwright.sync_api import expect

SELECTED = 'Kilo Mike Seven Golf Hotel Sierra'


def select_words(locator, selected=SELECTED):
    # Establish a visible, focused transcript before creating the selection,
    # just as a user would when selecting text with the mouse.
    locator.click()
    locator.evaluate('''(node, selected) => {
        const text = node.firstChild;
        const start = text.textContent.indexOf(selected);
        const range = document.createRange();
        range.setStart(text, start); range.setEnd(text, start + selected.length);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        if (selection.toString() !== selected) throw new Error('Transcript selection was not established');
    }''', selected)


def test_log_selection_correction_survives_refresh_and_updates_history(page, application):
    origin, ids = application
    page.goto(origin + '/')
    page.evaluate("activatePanel('transcripts')")
    card = page.locator('[data-source-path="a-correction-log.wav"]')
    expect(card).to_contain_text(SELECTED)
    select_words(card.locator('.transcript'))
    # Polling must not replace the selected DOM node before the user clicks.
    page.evaluate('loadJobs()')
    assert page.evaluate('window.getSelection().toString()') == SELECTED
    expect(card.locator('.transcript')).to_contain_text(SELECTED)
    card.get_by_role('button', name='Correct callsign', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog.locator('.correction-selection')).to_have_text('Selected: ' + SELECTED)
    dialog.get_by_label('Correct callsign', exact=True).fill('K2ABC')
    dialog.get_by_role('button', name='Save correction').click()
    expect(dialog).not_to_be_visible()
    expect(card.locator('.transcript')).to_contain_text('Hello K2ABC,')
    page.reload()
    expect(card.locator('.transcript')).to_contain_text('Hello K2ABC,')
    saved = page.request.get(origin + '/api/v1/archive/recordings/' + ids['correction_log']).json()
    assert SELECTED in saved['transcript']['raw_text']
    assert saved['transcript']['callsign_mentions'][0]['callsign'] == 'K2ABC'
    page.goto(origin + '/callsigns/K2ABC')
    expect(page.locator('#history')).to_contain_text('corrected')
    expect(page.locator('#history')).to_contain_text(SELECTED)


def test_archive_segment_selection_mobile_editor_and_validation(page, application):
    origin, ids = application
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(origin + '/archive/recordings/' + ids['correction_detail'])
    segment = page.locator('.transcript-segment > p').first
    expect(segment).to_contain_text(SELECTED)
    select_words(segment)
    page.get_by_role('button', name='Correct callsign', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog.locator('.correction-selection')).to_contain_text(SELECTED)
    dialog.get_by_label('Correct callsign', exact=True).fill('<script>')
    dialog.get_by_role('button', name='Save correction').click()
    expect(dialog.locator('[role=status]')).to_contain_text('letters and digits')
    dialog.get_by_label('Correct callsign', exact=True).fill('K3ABC')
    assert dialog.bounding_box()['width'] <= 390
    dialog.get_by_role('button', name='Save correction').click()
    expect(dialog).not_to_be_visible()
    expect(segment).to_contain_text('Hello K3ABC,')
    page.get_by_role('button', name='Raw transcript', exact=True).click()
    expect(page.locator('pre.transcript-text:visible')).to_contain_text(SELECTED)
    page.reload()
    expect(page.locator('.transcript-segment').first).to_contain_text('Hello K3ABC,')


def test_viewer_has_no_transcript_correction_controls(page, application):
    origin, ids = application
    page.context.clear_cookies()
    page.context.add_cookies([{'name': '__Host-aslt_session', 'value': 'browser-viewer', 'url': origin, 'secure': True}])
    page.goto(origin + '/archive/recordings/' + ids['correction_detail'])
    expect(page.locator('#recording-detail')).to_be_visible()
    expect(page.get_by_role('button', name='Correct callsign', exact=True)).to_have_count(0)
    page.goto(origin + '/')
    expect(page.locator('#recordings .recording').first).to_be_attached()
    expect(page.get_by_role('button', name='Correct callsign', exact=True)).to_have_count(0)
