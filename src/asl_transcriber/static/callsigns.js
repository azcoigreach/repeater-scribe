const directory = document.querySelector('#callsign-directory');
const state = document.querySelector('#callsign-state');
const more = document.querySelector('#callsign-more');
let cursor = null;
function text(tag, value, className = '') { const node = document.createElement(tag); node.textContent = value ?? ''; if (className) node.className = className; return node; }
async function load(reset = false) {
  if (reset) { cursor = null; directory.replaceChildren(); }
  const query = new URLSearchParams({ limit: '50' }); const q = document.querySelector('#callsign-query').value.trim();
  if (q) query.set('q', q); if (document.querySelector('#callsign-sort').value === 'alphabetical') query.set('alphabetical', 'true'); if (cursor) query.set('cursor', cursor);
  const response = await fetch(`/api/v1/callsigns?${query}`); if (!response.ok) { state.textContent = 'Could not load callsigns.'; return; }
  const data = await response.json(); state.textContent = data.items.length ? '' : 'No callsigns found.';
  data.items.forEach(item => { const card = document.createElement('article'); card.className = 'recording'; const link = document.createElement('a'); link.href = `/callsigns/${encodeURIComponent(item.callsign)}`; link.append(text('h3', item.callsign)); card.append(link, text('p', `${item.mention_count} mentions across ${item.recording_count} recordings · ${item.active_days} active days`), text('p', `Last heard: ${item.last_heard ? new Date(item.last_heard).toLocaleString() : 'Time unavailable'}`)); directory.append(card); });
  cursor = data.next_cursor; more.hidden = !data.has_more;
}
document.querySelector('#callsign-search').addEventListener('submit', event => { event.preventDefault(); load(true); }); more.addEventListener('click', () => load()); load(true);
