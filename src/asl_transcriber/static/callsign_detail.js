const name = window.callsignName;
const role = window.callsignRole;
const profile = document.querySelector('#profile');
const history = document.querySelector('#history');
const state = document.querySelector('#history-state');
const filters = document.querySelector('#history-filters');
const more = document.querySelector('#history-more');
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
let cursor = null;
const text = (tag, value, className = '') => { const node = document.createElement(tag); node.textContent = value ?? ''; if (className) node.className = className; return node; };
const timestamp = value => value ? new Date(value).toLocaleString() : 'Time unavailable';
const operator = () => role === 'operator' || role === 'admin';

async function review(mention, action) {
  const body = { action };
  if (action === 'correct') { const corrected = prompt('Corrected callsign'); if (!corrected) return; body.corrected_callsign = corrected; }
  const response = await fetch(`/ui/callsign-mentions/${encodeURIComponent(mention.mention_id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }, body: JSON.stringify(body) });
  if (!response.ok) { state.textContent = 'Could not update mention review.'; return; }
  loadHistory(true); loadProfile();
}

function renderMention(mention) {
  const card = document.createElement('article'); card.className = 'recording';
  card.append(text('h3', timestamp(mention.heard_at)), text('p', `${mention.review_status} · ${mention.timing_precision || 'recording'} timing`), text('p', mention.excerpt || 'Mention recorded'), text('p', `Offsets: ${mention.start_offset ?? 'unavailable'}s to ${mention.end_offset ?? 'unavailable'}s`));
  const link = text('a', 'Open recording'); link.href = mention.recording_url; card.append(link);
  if (mention.audio_available && mention.start_offset != null) { const seek = text('button', 'Play from mention'); seek.type = 'button'; seek.addEventListener('click', () => { const audio = new Audio(`/api/v1/archive/recordings/${encodeURIComponent(mention.recording_id)}/audio`); audio.addEventListener('loadedmetadata', () => { audio.currentTime = Number(mention.start_offset); audio.play().catch(() => {}); }, { once: true }); }); card.append(seek); } else card.append(text('p', `Audio ${mention.audio_status || 'unavailable'}`, 'muted-text'));
  if (operator()) ['confirm', 'reject', 'correct'].forEach(action => { const button = text('button', action[0].toUpperCase() + action.slice(1)); button.type = 'button'; button.addEventListener('click', () => review(mention, action)); card.append(button); });
  return card;
}

async function loadHistory(reset = false) {
  if (reset) { cursor = null; history.replaceChildren(); }
  const query = new URLSearchParams({ limit: '50' }); new FormData(filters).forEach((value, key) => { if (value) query.set(key, String(value)); }); if (cursor) query.set('cursor', cursor);
  const response = await fetch(`/api/v1/callsigns/${encodeURIComponent(name)}/mentions?${query}`);
  if (!response.ok) { state.textContent = 'Could not load mention history.'; return; }
  const data = await response.json(); data.items.forEach(mention => history.append(renderMention(mention))); cursor = data.next_cursor; more.hidden = !data.has_more; state.textContent = history.children.length ? '' : 'No mention history matches these filters.';
}

async function loadProfile() {
  const response = await fetch(`/api/v1/callsigns/${encodeURIComponent(name)}`); if (!response.ok) { document.querySelector('#profile-location').textContent = 'Callsign not found.'; return; }
  const item = await response.json(); document.querySelector('#profile-callsign').textContent = item.callsign; document.querySelector('#profile-location').textContent = item.qrz_location || 'QRZ location unavailable'; profile.replaceChildren();
  if (item.qrz_image_url) { const image = document.createElement('img'); image.src = item.qrz_image_url; image.alt = `QRZ profile image for ${item.callsign}`; image.referrerPolicy = 'no-referrer'; profile.append(image); }
  if (item.qrz_profile_url) { const qrz = text('a', 'View QRZ profile'); qrz.href = item.qrz_profile_url; qrz.target = '_blank'; qrz.rel = 'noopener noreferrer'; profile.append(qrz); }
  profile.append(text('p', `First heard: ${timestamp(item.first_heard)} · Last heard: ${timestamp(item.last_heard)}`), text('p', `${item.total_mentions} mentions · ${item.unique_recordings} recordings · ${item.active_days} active days`), text('p', `Detected: ${item.detected_mentions} · Confirmed: ${item.confirmed_mentions} · Corrected: ${item.corrected_mentions} · Rejected: ${item.rejected_mentions}`), text('p', `Attributed transmissions: ${item.attributed_transmission_count} · Airtime: ${item.attributed_airtime_seconds}s`), text('p', item.attribution_complete ? 'Attribution coverage is complete.' : 'Attribution coverage is unavailable or incomplete.'));
  const confidence = item.confidence_summary || {}; profile.append(text('p', `Confidence: ${(confidence.minimum ?? 0).toFixed(2)} to ${(confidence.maximum ?? 0).toFixed(2)}, average ${(confidence.average ?? 0).toFixed(2)}`));
  if (operator()) { const refresh = text('button', 'Refresh QRZ'); refresh.type = 'button'; refresh.addEventListener('click', async () => { const reply = await fetch(`/ui/callsigns/${encodeURIComponent(name)}/qrz-refresh`, { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } }); if (reply.ok) loadProfile(); else state.textContent = 'Could not refresh QRZ profile.'; }); profile.append(refresh); }
}

filters.addEventListener('submit', event => { event.preventDefault(); loadHistory(true); }); more.addEventListener('click', () => loadHistory()); loadProfile(); loadHistory(true);
