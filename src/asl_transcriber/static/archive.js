(() => {
  const form = document.querySelector('#archive-filters');
  const rows = document.querySelector('#recordings');
  const cards = document.querySelector('#recording-cards');
  const state = document.querySelector('#archive-state');
  const count = document.querySelector('#loaded-count');
  const more = document.querySelector('#load-more');
  const chips = document.querySelector('#active-filters');
  const filterError = document.querySelector('#filter-error');
  const fields = [...form.querySelectorAll('input, select')];
  let cursor = null;
  let loading = false;
  let controller = null;
  let requestVersion = 0;
  let loaded = new Set();
  let debounce = null;

  const value = name => form.elements[name].value.trim();
  const formatTime = iso => iso ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(iso)) : 'Unknown time';
  const formatDuration = seconds => { if (seconds == null) return '—'; const n = Math.round(seconds); return [Math.floor(n / 3600), Math.floor(n / 60) % 60, n % 60].map((part, index) => index === 0 ? String(part) : String(part).padStart(2, '0')).filter((part, index, all) => index || all[0] !== '0').join(':'); };
  const formatSize = bytes => { if (bytes == null) return '—'; const units = ['B', 'KB', 'MB', 'GB']; let size = bytes; let unit = 0; while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; } return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`; };
  const addText = (element, text) => { element.textContent = text || '—'; return element; };
  const tag = (text, type) => { const span = document.createElement('span'); span.className = `status-tag ${type || ''}`; span.textContent = text || 'unknown'; return span; };
  const callsigns = item => { const seen = new Set(); return (item.transcript?.callsign_mentions || []).map(mention => String(mention.callsign || '').toUpperCase()).filter(call => call && !seen.has(call) && seen.add(call)); };
  const callNodes = item => { const fragment = document.createDocumentFragment(); callsigns(item).forEach(call => { const button = document.createElement('button'); button.type = 'button'; button.className = 'callsign'; button.textContent = call; button.addEventListener('click', () => { form.elements.callsign.value = call; resetAndLoad(true); }); fragment.append(button); }); return fragment; };
  const params = () => { const result = new URLSearchParams(); ['q', 'status', 'audio_status', 'callsign'].forEach(name => { if (value(name)) result.set(name, value(name)); }); const from = value('from'); const to = value('to'); if (from) result.set('from', new Date(`${from}T00:00:00`).toISOString()); if (to) result.set('to', new Date(`${to}T23:59:59.999`).toISOString()); return result; };
  const restore = () => { const query = new URLSearchParams(location.search); ['q', 'status', 'audio_status', 'callsign'].forEach(name => { form.elements[name].value = query.get(name) ?? ''; }); ['from', 'to'].forEach(name => { const stored = query.get(name); form.elements[name].value = stored ? stored.slice(0, 10) : ''; }); };
  const updateUrl = push => { const query = new URLSearchParams(); fields.forEach(field => { if (field.value) query.set(field.name, field.value); }); const url = `${location.pathname}${query.size ? `?${query}` : ''}`; history[push ? 'pushState' : 'replaceState']({}, '', url); };
  const filterLabel = field => field.labels?.[0]?.firstChild?.textContent?.trim() || field.name;
  const renderChips = () => { chips.replaceChildren(); fields.filter(field => field.value).forEach(field => { const label = filterLabel(field); const chip = document.createElement('span'); chip.className = 'filter-chip'; chip.append(document.createTextNode(`${label}: ${field.value} `)); const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Clear'; remove.setAttribute('aria-label', `Clear ${label} filter`); remove.addEventListener('click', () => { field.value = ''; resetAndLoad(true); }); chip.append(remove); chips.append(chip); }); };
  const validRange = () => { const from = value('from'); const to = value('to'); if (from && to && from > to) { filterError.hidden = false; filterError.textContent = 'The start date must not be after the end date.'; return false; } filterError.hidden = true; return true; };
  const setState = (message, error = false) => { state.hidden = !message; state.textContent = message || ''; state.classList.toggle('error', error); };
  const recordingLink = id => `/archive/recordings/${encodeURIComponent(id)}`;
  const render = item => { if (loaded.has(item.id)) return; loaded.add(item.id); const row = document.createElement('tr'); const time = document.createElement('time'); time.dateTime = item.started_at || ''; time.title = item.started_at || 'No derived source timestamp'; addText(time, formatTime(item.started_at)); const cells = [time, formatDuration(item.duration_seconds), callNodes(item), item.local_node || item.source_node || '—', tag(item.status, item.status), tag(item.audio_status, item.audio_status), item.transcript?.display_text || item.transcript?.raw_text || 'No transcript']; cells.forEach((content, index) => { const cell = document.createElement('td'); if (index === 6) { const excerpt = document.createElement('p'); excerpt.className = 'excerpt'; addText(excerpt, content); cell.append(excerpt); } else if (content instanceof Node) cell.append(content); else addText(cell, content); row.append(cell); }); const action = document.createElement('td'); const link = document.createElement('a'); link.className = 'detail-link'; link.href = recordingLink(item.id); link.textContent = 'Open details'; action.append(link); row.append(action); rows.append(row);
    const card = document.createElement('article'); card.className = 'recording-card'; const header = document.createElement('header'); header.append(time.cloneNode(true), tag(item.audio_status, item.audio_status)); card.append(header); const meta = document.createElement('p'); meta.className = 'meta'; meta.textContent = `${formatDuration(item.duration_seconds)} · ${item.status}`; card.append(meta); const text = document.createElement('p'); text.className = 'excerpt'; addText(text, item.transcript?.display_text || item.transcript?.raw_text || 'No transcript'); card.append(text); const callLine = document.createElement('p'); callLine.append(callNodes(item)); card.append(callLine); const cardLink = link.cloneNode(true); card.append(cardLink); cards.append(card);
  };
  async function load(append = false) { if (append && loading) return; if (!append) { requestVersion += 1; controller?.abort(); controller = null; loading = false; rows.replaceChildren(); cards.replaceChildren(); loaded = new Set(); cursor = null; } if (!validRange()) return; loading = true; more.disabled = true; const version = ++requestVersion; controller = new AbortController(); const filterQuery = params(); const hasActiveFilters = filterQuery.size > 0; const query = new URLSearchParams(filterQuery); query.set('limit', '50'); if (append && cursor) query.set('cursor', cursor); if (!append) setState('Loading archive...'); try { const response = await fetch(`/api/v1/archive/recordings?${query}`, { signal: controller.signal, headers: { Accept: 'application/json' } }); if (version !== requestVersion) return; if (!response.ok) { if (response.status === 401 || response.status === 403) throw new Error('Your session cannot access the archive. Sign in again.'); if (response.status === 422 && append) throw new Error('The archive cursor is no longer valid. Restart the search.'); throw new Error('Archive request failed.'); } const payload = await response.json(); payload.items.forEach(render); cursor = payload.next_cursor; more.hidden = !payload.has_more; count.textContent = `${loaded.size} record${loaded.size === 1 ? '' : 's'} loaded`; setState(!loaded.size ? (hasActiveFilters ? 'No recordings match these filters.' : 'No recordings are in the catalog yet.') : ''); } catch (error) { if (error.name !== 'AbortError') { setState(error.message, true); } } finally { if (version === requestVersion) { loading = false; more.disabled = false; } } }
  function resetAndLoad(push) { if (!validRange()) return; updateUrl(push); renderChips(); load(false); }
  form.addEventListener('submit', event => { event.preventDefault(); resetAndLoad(true); });
  ['q', 'callsign'].forEach(name => form.elements[name].addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(() => resetAndLoad(true), 300); }));
  ['from', 'to', 'status', 'audio_status'].forEach(name => form.elements[name].addEventListener('change', () => resetAndLoad(true)));
  document.querySelector('#clear').addEventListener('click', () => { form.reset(); resetAndLoad(true); });
  document.querySelector('#refresh').addEventListener('click', () => load(false));
  more.addEventListener('click', () => load(true));
  window.addEventListener('popstate', () => { clearTimeout(debounce); restore(); renderChips(); load(false); });
  document.querySelector('#sign-out')?.addEventListener('click', async () => { await fetch('/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content } }); location.assign('/auth/login'); });
  restore(); renderChips(); load(false);
})();
