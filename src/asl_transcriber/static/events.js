(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const canWrite = ['operator', 'admin'].includes(document.body.dataset.role);
  if (!canWrite) document.querySelectorAll('[data-write]').forEach(node => node.hidden = true);
  const id = location.pathname.split('/')[2] || null;
  const query = new URLSearchParams(location.search);
  const form = $('#event-form');
  let current = null, editing = false, creationKey = null, markerId = null, checkinId = null;
  let selections = [], listCursor = null;
  const collections = {};
  const types = ['Net', 'Exercise', 'Club Event', 'POTA', 'Testing', 'Maintenance', 'Roundtable', 'Special Event Station', 'QSO Session', 'Custom'];
  const iso = input => UITime.inputUTC(input);
  const time = value => value ? UITime.format(value) : 'Unknown time';
  const seconds = value => value == null ? 'Unavailable' : `${Math.round(value)} seconds`;
  const tags = value => value.split(',').map(v => v.trim()).filter(Boolean);
  const element = (tag, value, className) => { const node = document.createElement(tag); if (value != null) node.textContent = value; if (className) node.className = className; return node; };
  const link = (label, href) => { const node = element('a', label); node.href = href; return node; };
  const button = (label, action) => { const node = element('button', label, 'quiet-button'); node.type = 'button'; node.addEventListener('click', () => run(action)); return node; };
  const error = message => { $('#event-error').textContent = message; $('#event-error').hidden = !message; };
  async function run(action) { error(''); try { await action(); } catch (e) { error(e.message); } }
  async function api(path = '', method = 'GET', body, headers = {}) {
    const response = await fetch(`${method === 'GET' ? '/api/v1' : '/ui'}/sessions${path}`, {
      method, headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': $('meta[name="csrf-token"]').content, ...headers },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
    if (!response.ok) {
      let payload = {}; try { payload = await response.json(); } catch (_) {}
      const detail = payload.detail;
      throw new Error(typeof detail === 'string' ? detail : response.status === 422 ? 'Check required fields, timestamps, callsigns and audio offsets.' : `Request failed (${response.status}). Please retry.`);
    }
    return response.json();
  }
  const field = (target, name) => target.elements.namedItem(name);
  function fill(target, values) { Object.entries(values).forEach(([key, value]) => { const input = field(target, key); if (input?.type === 'datetime-local') UITime.setInput(input, value); else if (input) input.value = value ?? ''; }); }
  function editor(historical = false, edit = false) {
    editing = edit;
    $('#event-editor').hidden = false;
    $('#editor-title').textContent = edit ? 'Edit event' : historical ? 'Create Historical Event' : 'Start Event';
    $('#save-event').textContent = edit ? 'Save event' : historical ? 'Create Historical Event' : 'Start Event';
    $('#preview-event').hidden = edit;
    $('#membership-preview').replaceChildren();
    $('#event-save-status').hidden = true;
    creationKey = crypto.randomUUID();
    form.reset();
    field(form, 'source_id').disabled = edit;
    field(form, 'ended_at').required = historical;
    fill(form, edit ? {...current, tags: current.tags.join(', ')} : {
      name: '', started_at: new Date(Date.now() - (historical ? 3600000 : 0)),
      ended_at: historical ? new Date() : '', source_id: query.get('source_id') || field(form, 'source_id').options[0]?.value,
    });
    if (!edit) {
      if (query.get('from')) UITime.setInput(field(form, 'started_at'), query.get('from'));
      if (query.get('to')) UITime.setInput(field(form, 'ended_at'), query.get('to'));
    }
    $('#selection-note').textContent = selections.length && !edit ? `${selections.length} selected recording(s) will be explicit inclusions. Preview shows additional automatic membership.` : '';
    $('#event-editor').scrollIntoView({block:'start'});
  }
  function payload() {
    return { name: field(form, 'name').value, type: field(form, 'type').value, source_id: field(form, 'source_id').value,
      started_at: iso(field(form, 'started_at')), ended_at: iso(field(form, 'ended_at')),
      net_control: field(form, 'net_control').value || null, description: field(form, 'description').value,
      tags: tags(field(form, 'tags').value), recording_ids: selections };
  }
  async function preview() {
    if (!['source_id','started_at','ended_at'].every(name => field(form, name).reportValidity())) return;
    const data = payload();data.name ||= 'Membership preview';
    const result = await api('/preview', 'POST', data);
    const box = $('#membership-preview'); box.replaceChildren(element('p', `${result.explicit_inclusion_count} explicit inclusion(s); ${result.automatic_count} automatic match(es), including ${result.additional_automatic_count} additional recording(s).`), element('p', result.explanation));
    result.additional_examples.forEach(row => box.append(element('p', `${time(row.started_at)} · ${row.source_path}${row.boundary_crossing ? ' · whole boundary-crossing recording' : ''}`)));
    return result;
  }
  $('#start-event').addEventListener('click', () => editor());
  $('#historical-event').addEventListener('click', () => editor(true));
  $('#cancel-event').addEventListener('click', () => $('#event-editor').hidden = true);
  $('#preview-event').addEventListener('click', () => run(preview));
  form.addEventListener('submit', e => { e.preventDefault(); if ($('#save-event').disabled) return; run(async () => {
    const data = payload();
    const save = $('#save-event');
    const label = save.textContent;
    const status = $('#event-save-status');
    save.disabled = true;
    save.textContent = editing ? 'Saving…' : 'Creating…';
    status.textContent = editing ? 'Saving event…' : 'Creating event…';
    status.hidden = false;
    try {
      if (editing) {
        const {tags: values, source_id, recording_ids, ...metadata} = data;
        await api(`/${id}`, 'PATCH', metadata);
        await api(`/${id}/tags`, 'PATCH', {tags: values});
        $('#event-editor').hidden = true;
        await refreshDetail(); await loadCollection('recordings');
      } else {
        const result = await api('', 'POST', data, {'Idempotency-Key': creationKey});
        sessionStorage.removeItem('event-selection');
        location.assign(`/events/${result.id}`);
      }
    } catch (failure) {
      status.textContent = `Could not ${editing ? 'save' : 'create'} event: ${failure.message}`;
      throw failure;
    } finally { save.disabled = false; save.textContent = label; }
  }); });
  form.addEventListener('input', () => $('#membership-preview').replaceChildren());
  async function loadEvents(append = false) {
    const filters = new URLSearchParams();
    for (const [key, value] of new FormData($('#event-filters'))) if (value) filters.set(key, ['from', 'to'].includes(key) ? iso(field($('#event-filters'), key)) : value);
    for (const key of ['recording_id', 'callsign']) if (query.get(key)) filters.set(key, query.get(key));
    if (append && listCursor) filters.set('cursor', listCursor);
    const result = await api(`?${filters}`);
    if (!append) $('#events-list').replaceChildren();
    result.items.forEach(row => {
      const card = element('article', null, 'event-card');
      const heading = element('h2'); heading.append(link(row.name, `/events/${row.id}`));
      card.append(heading, element('p', `${row.status.toUpperCase()} · ${row.type} · ${row.source_label}`), element('p', `${time(row.started_at)} → ${row.ended_at ? time(row.ended_at) : 'Active'}`), element('p', row.tags.join(' · ')));
      $('#events-list').append(card);
    });
    listCursor = result.next_cursor; $('#more-events').hidden = !result.has_more;
    $('#event-state').textContent = $('#events-list').children.length ? '' : 'No events match. Start an event or create one from historical recordings.';
  }
  $('#event-filters').addEventListener('submit', e => {e.preventDefault();run(() => loadEvents());});
  $('#clear-event-filters').addEventListener('click', () => {$('#event-filters').reset();run(() => loadEvents());});
  $('#more-events').addEventListener('click', () => run(() => loadEvents(true)));
  async function refreshDetail() {
    current = await api(`/${id}`);
    $('#event-title').textContent = current.name;
    $('#event-subtitle').textContent = `${current.status.toUpperCase()} · ${current.type} · ${current.source_label} · ${time(current.started_at)} → ${current.ended_at ? time(current.ended_at) : 'Now'}${current.net_control ? ` · Net control ${current.net_control}` : ''}`;
    $('#event-description').textContent = current.description;
    $('#event-tags').textContent = current.tags.length ? `Tags: ${current.tags.join(', ')}` : 'No tags';
    $('#event-metrics').replaceChildren(element('p', `Event elapsed: ${seconds(current.elapsed_seconds)}`), element('p', `Included recording audio: ${seconds(current.included_audio_seconds)}${current.unknown_audio_duration_count ? ` + ${current.unknown_audio_duration_count} unknown duration(s)` : ''}`), element('p', `Station-attributed airtime: ${seconds(current.station_attributed_airtime_seconds)}`), element('p', `${current.recording_count} included recording(s)`));
    $('#end-event').hidden = current.status !== 'active'; $('#reopen-event').hidden = current.status === 'active';
    $('#event-state').textContent = '';
  }
  $('#edit-event').addEventListener('click', () => editor(false, true));
  $('#end-event').addEventListener('click', () => run(async () => {await api(`/${id}/end`, 'POST', {});await refreshDetail();await loadCollection('recordings');}));
  $('#reopen-event').addEventListener('click', () => run(async () => {await api(`/${id}/reopen`, 'POST', {});await refreshDetail();await loadCollection('recordings');}));
  const archiveLink = (recording, offset = 0) => `/archive/recordings/${encodeURIComponent(recording)}?offset=${Number(offset) || 0}`;
  function seek(audio, offset) { if (!audio) return; audio.currentTime = Number(offset) || 0; audio.play().catch(() => {}); }
  function renderRecording(row) {
    const card = element('article', null, 'event-card'); card.dataset.recordingId = row.id;
    const heading = element('h3'); heading.append(link(time(row.started_at), archiveLink(row.id))); card.append(heading);
    card.append(element('p', `${row.source_path} · ${seconds(row.duration_seconds)} · ${row.decision}${row.included ? '' : ' · EXCLUDED'}`));
    if (row.boundary_crossing) card.append(element('p', 'Crosses event boundary · whole recording, audio not trimmed', 'event-warning'));
    let audio = null;
    if (row.audio_available) {
      audio = document.querySelector(`[data-recording-id="${CSS.escape(row.id)}"] audio`) || element('audio');
      if (!audio.src) { audio.controls = true; audio.preload = 'metadata'; audio.src = `/api/v1/archive/recordings/${encodeURIComponent(row.id)}/audio`; audio.addEventListener('error', () => {audio.replaceWith(element('p', 'Audio unavailable. Transcript and event history remain saved.', 'event-warning'));}); }
      card.append(audio);
    } else card.append(element('p', `Audio unavailable (${row.audio_status}). Saved transcript and history remain readable.`, 'event-warning'));
    if (row.provisional) card.append(element('p', 'Provisional live transcript', 'event-warning'));
    const transcript = row.transcript;
    if (transcript?.segments?.length) transcript.segments.forEach(segment => {
      const line = element('div', null, 'event-segment'); const control = button(`${Number(segment.start).toFixed(2)}s`, () => seek(audio, segment.start)); control.disabled = !audio;
      line.append(control, element('p', segment.display_text || segment.raw_text)); card.append(line);
    });
    else card.append(element('p', transcript?.display_text || transcript?.raw_text || 'Waiting for a saved transcript…', 'event-transcript'));
    if (canWrite) {
      const actions = element('div', null, 'event-actions');
      ['include', 'exclude', 'automatic'].forEach(decision => actions.append(button(decision === 'automatic' ? 'Automatic' : decision === 'include' ? 'Include' : 'Exclude', async () => {
        await api(`/${id}/recordings/${row.id}`, 'PATCH', {decision}); await loadCollection('recordings'); await refreshDetail(); await loadCollection('checkins'); await loadCollection('detected');
      })));
      actions.append(button('Mark audio position', () => {
        const offset = audio?.currentTime || 0; fill($('#marker-form'), {recording_id: row.id, audio_offset: offset, at: row.started_at ? new Date(UITime.instant(row.started_at).getTime() + offset * 1000) : new Date()});
        $('#marker-form').scrollIntoView({block:'center'});field($('#marker-form'), 'note').focus();
      })); card.append(actions);
      const tagForm = element('form', null, 'tag-form'); const label = element('label', 'Recording tags '); const input = element('input'); input.value = row.tags.join(', '); input.setAttribute('aria-label', `Tags for ${row.source_path}`); label.append(input); const save = element('button', 'Save recording tags', 'quiet-button'); tagForm.append(label, save);
      tagForm.addEventListener('submit', e => {e.preventDefault();run(async () => {await api(`/${id}/recordings/${row.id}/tags`, 'PATCH', {tags: tags(input.value)}); save.textContent = 'Tags saved';});});card.append(tagForm);
    } else card.append(element('p', `Tags: ${row.tags.join(', ') || 'None'}`));
    return card;
  }
  function annotationCard(row, kind) {
    const card = element('article', null, 'event-card');
    const isMarker = kind === 'markers';
    card.append(element('h3', isMarker ? `${row.type}${row.callsign ? ` · ${row.callsign}` : ''}` : row.callsign), element('p', `${time(row.at)} · ${row.note}`));
    if (!isMarker) card.append(link('Station history', `/callsigns/${encodeURIComponent(row.callsign)}`), element('p', `Confirmed by ${row.confirmed_by} at ${time(row.confirmed_at)}`));
    if (row.evidence_outside_event) card.append(element('p', 'Supporting recording is no longer included in this event. Review this evidence.', 'event-warning'));
    if (row.recording_id) card.append(link(row.audio_available ? `Play marker / evidence at ${Number(row.audio_offset || 0).toFixed(2)}s` : 'View saved recording (audio unavailable)', archiveLink(row.recording_id, row.audio_offset)));
    else card.append(element('p', 'No audio anchor yet; absolute time is saved.'));
    if (canWrite) {
      const actions = element('div', null, 'event-actions');
      actions.append(button(isMarker ? 'Edit marker' : 'Edit check-in', () => {
        const target = isMarker ? $('#marker-form') : $('#checkin-form');
        if (isMarker) {markerId = row.id;$('#save-marker').textContent = 'Save marker';} else {checkinId = row.id;$('#save-checkin').textContent = 'Save check-in';}
        fill(target, row); target.scrollIntoView({block:'center'});
      }), button(isMarker ? 'Remove marker' : 'Undo check-in', async () => {await api(`/${id}/${kind}/${row.id}`, 'DELETE');await loadCollection(kind);}));
      card.append(actions);
    }
    return card;
  }
  function renderDetected(row) {
    const card = element('article', null, 'event-card'); card.append(link(row.callsign, `/callsigns/${encodeURIComponent(row.callsign)}`), element('p', `${row.mention_count} mention(s) in ${row.recording_count} recording(s) · First ${time(row.first_mention_at)} · Last ${time(row.last_mention_at)}`), element('p', `Attributed transmissions: ${row.attributed_transmission_count ?? 'Unavailable'} · Attributed airtime: ${seconds(row.attributed_airtime_seconds)}${row.attribution_status === 'partial' ? ' (partial evidence)' : ''}`));
    row.evidence.forEach((evidence, index) => card.append(link(`Evidence ${index + 1} `, archiveLink(evidence.recording_id, evidence.start_offset))));
    if (canWrite) card.append(button('Confirm Check-In', () => {
      checkinId = null; $('#save-checkin').textContent = 'Confirm Check-In';
      fill($('#checkin-form'), {callsign: row.callsign, at: row.first_mention_at || new Date(), note: '', recording_id: row.evidence[0]?.recording_id, audio_offset: row.evidence[0]?.start_offset});
      $('#checkin-form').scrollIntoView({block:'center'});field($('#checkin-form'), 'at').focus();
    }));
    return card;
  }
  async function loadCollection(kind, append = false, background = false) {
    const state = collections[kind] ||= {cursor:null, lastQuery:'', busy:false, lastKeys:[], waiters:[]};
    if (state.busy) {
      if (background) return;
      await new Promise(resolve => state.waiters.push(resolve));
      return loadCollection(kind, append, background);
    }
    state.busy = true;
    try {
      const params = new URLSearchParams({limit:'25'});
      if (kind === 'recordings') {params.set('membership', $('#membership-view').value);if ($('#latest-traffic').checked) params.set('latest', 'true');}
      if (append && state.cursor) params.set('cursor', state.cursor);
      const requestQuery = background ? state.lastQuery : params.toString();
      const result = await api(`/${id}/${kind}?${requestQuery}`);
      const root = $(`#${kind}-list`);
      if (!append && !background) root.replaceChildren();
      const incomingKeys = new Set(result.items.map(row => row.id || row.callsign));
      if (background) [...root.children].filter(node => state.lastKeys.includes(node.dataset.key) && !incomingKeys.has(node.dataset.key)).forEach(node => node.remove());
      result.items.forEach(row => {
        const key = row.id || row.callsign;
        const existing = [...root.children].find(node => node.dataset.key === key);
        // Rebuild only changed rows; keep any existing audio element and playback position.
        const signature = JSON.stringify(row);
        if (existing?.dataset.signature === signature) return;
        const node = kind === 'recordings' ? renderRecording(row) : kind === 'detected' ? renderDetected(row) : annotationCard(row, kind);
        node.dataset.key = key; node.dataset.signature = signature;
        if (existing) existing.replaceWith(node); else root.append(node);
      });
      if (!root.children.length) root.append(element('p', kind === 'recordings' ? 'No recordings yet. Membership updates as recordings arrive.' : kind === 'detected' ? 'No current callsign mentions in included recordings.' : kind === 'checkins' ? 'No operator-confirmed check-ins yet.' : 'No markers yet.', 'empty-collection'));
      if (result.items.length) root.querySelector('.empty-collection')?.remove();
      state.cursor = result.next_cursor; state.lastQuery = requestQuery; state.lastKeys = [...incomingKeys];
      if (kind === 'recordings') $('#earlier-recordings').hidden = !result.has_earlier;
      $(`#more-${kind}`).hidden = !result.has_more;
    } finally {state.busy = false;state.waiters.splice(0).forEach(resolve => resolve());}
  }
  for (const kind of ['recordings', 'markers', 'detected', 'checkins']) $(`#more-${kind}`).addEventListener('click', () => run(() => loadCollection(kind, true)));
  $('#refresh-recordings').addEventListener('click', () => run(async () => {await loadCollection('recordings');await refreshDetail();}));
  $('#membership-view').addEventListener('change', () => run(() => loadCollection('recordings')));
  $('#latest-traffic').addEventListener('change', () => run(() => loadCollection('recordings')));
  $('#include-recording').addEventListener('submit', e => {e.preventDefault();run(async () => {await api(`/${id}/recordings/${encodeURIComponent(field(e.target, 'recording_id').value)}`, 'PATCH', {decision:'include'});e.target.reset();await loadCollection('recordings');await refreshDetail();});});
  function resetAnnotation(kind) {
    const isMarker = kind === 'markers'; const target = isMarker ? $('#marker-form') : $('#checkin-form');
    target.reset();UITime.setInput(field(target, 'at'), new Date());
    if (isMarker) {markerId = null;$('#save-marker').textContent = 'Add marker';} else {checkinId = null;$('#save-checkin').textContent = 'Confirm Check-In';}
  }
  for (const kind of ['markers', 'checkins']) {
    const isMarker = kind === 'markers'; const target = isMarker ? $('#marker-form') : $('#checkin-form');
    $(isMarker ? '#cancel-marker' : '#cancel-checkin').addEventListener('click', () => resetAnnotation(kind));
    target.addEventListener('submit', e => {e.preventDefault();run(async () => {
      const values = Object.fromEntries(new FormData(target)); values.at = iso(field(target, 'at')); values.recording_id ||= null; values.audio_offset = values.audio_offset === '' ? null : Number(values.audio_offset);
      if (isMarker) values.callsign ||= null;
      const identifier = isMarker ? markerId : checkinId;
      await api(`/${id}/${kind}${identifier ? `/${identifier}` : ''}`, identifier ? 'PATCH' : 'POST', values);
      resetAnnotation(kind);await loadCollection(kind);
    });});
    resetAnnotation(kind);
  }
  $('#sign-out')?.addEventListener('click', async () => {await fetch('/auth/logout', {method:'POST', headers:{'X-CSRF-Token':$('meta[name="csrf-token"]').content}});location.assign('/auth/login');});
  run(async () => {
    document.querySelectorAll('.event-types').forEach(select => types.forEach(value => select.add(new Option(value, value))));
    const sourceData = await api('/sources');
    document.querySelectorAll('.event-sources').forEach(select => sourceData.items.forEach(value => select.add(new Option(value.label, value.id))));
    if (id) {
      $('#events-list-view').hidden = true;$('#event-detail').hidden = false;
      $('#start-event').hidden = true;$('#historical-event').hidden = true;
      await refreshDetail();$('#latest-traffic').checked = current.status === 'active';await Promise.all(['recordings','markers','detected','checkins'].map(kind => loadCollection(kind)));
      setInterval(() => {
        if (document.hidden) return;
        run(async () => {await refreshDetail();$('#latest-traffic').checked = current.status === 'active';await Promise.all(['recordings','markers','detected','checkins'].map(kind => loadCollection(kind, false, true)));});
      }, 5000);
    } else {
      for (const [key, value] of query) if (field($('#event-filters'), key)) fill($('#event-filters'), {[key]: value});
      await loadEvents();
      if (query.get('create') && canWrite) {
        if (query.get('selection')) {try {selections = JSON.parse(sessionStorage.getItem('event-selection') || '[]');} catch (_) {selections = [];}}
        editor(query.get('create') === 'historical');
        if (selections.length) await preview();
      }
    }
  });
})();
