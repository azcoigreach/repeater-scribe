const directory = document.querySelector('#callsign-directory');
const state = document.querySelector('#callsign-state');
const more = document.querySelector('#callsign-more');
const callsignPattern = /^[A-Z0-9]{1,3}\d[A-Z]{1,4}$/;
let cursor = null;
let loading = false;
let requestVersion = 0;
let loaded = new Set();
function text(tag, value, className = '') { const node = document.createElement(tag); node.textContent = value ?? ''; if (className) node.className = className; return node; }
async function load(reset = false) {
  if (!reset && loading) return;
  const version = ++requestVersion; loading = true; more.disabled = true;
  if (reset) { cursor = null; loaded = new Set(); directory.replaceChildren(); }
  try {
    const query = new URLSearchParams({ limit: '50' }); const q = document.querySelector('#callsign-query').value.trim();
    if (q) query.set('q', q); if (document.querySelector('#callsign-sort').value === 'alphabetical') query.set('alphabetical', 'true'); if (cursor) query.set('cursor', cursor);
    const response = await fetch(`/api/v1/callsigns?${query}`); if (version !== requestVersion) return; if (!response.ok) { state.textContent = 'Could not load callsigns.'; return; }
    const data = await response.json(); if (version !== requestVersion) return; state.textContent = data.items.length ? '' : 'No callsigns found.';
    data.items.forEach(item => {
      if (loaded.has(item.callsign)) return; loaded.add(item.callsign);
      const card = text('article', '', 'recording');
      const link = text('a', '', 'callsign-evidence'); link.href = `/callsigns/${encodeURIComponent(item.callsign)}`; link.append(text('h3', item.callsign));
      const timestamp = value => value ? UITime.format(value) : 'Time unavailable';
      card.append(link, text('p', item.qrz_display_name || 'QRZ name unavailable'), text('p', item.qrz_location || 'QRZ location unavailable'),
        text('p', `${item.mention_count} mentions across ${item.recording_count} recordings · ${item.active_days} active days`),
        text('p', `First heard: ${timestamp(item.first_heard)} · Last heard: ${timestamp(item.last_heard)}`),
        text('p', `Most recent confidence: ${item.most_recent_confidence == null ? 'Unavailable' : (item.most_recent_confidence * 100).toFixed(0) + '%'} · ${item.confirmed_mentions} human-confirmed mentions`));
      directory.append(card);
    });
    cursor = data.next_cursor; more.hidden = !data.has_more;
  } catch (_) { if (version === requestVersion) state.textContent = 'Could not load callsigns.'; }
  finally { if (version === requestVersion) { loading = false; more.disabled = false; } }
}
document.querySelector('#callsign-search').addEventListener('submit', event => { event.preventDefault(); const query = document.querySelector('#callsign-query').value.trim().toUpperCase(); if (callsignPattern.test(query)) { location.assign(`/callsigns/${encodeURIComponent(query)}`); return; } load(true); }); more.addEventListener('click', () => load()); load(true);
