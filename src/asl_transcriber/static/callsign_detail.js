const workspace = document.querySelector('.archive-shell');
const name = workspace?.dataset.callsign;
const role = workspace?.dataset.role;
const profile = document.querySelector('#profile');
const history = document.querySelector('#history');
const state = document.querySelector('#history-state');
const filters = document.querySelector('#history-filters');
const more = document.querySelector('#history-more');
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
let cursor = null;
let historyLoading = false;
let historyVersion = 0;
let loadedMentions = new Set();
const player = new Audio();
let activePlaybackButton = null;
const text = (tag, value, className = '') => { const node = document.createElement(tag); node.textContent = value ?? ''; if (className) node.className = className; return node; };
const safeExternalUrl = value => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch (_) { return null; } };
const percent = value => value == null ? 'Unavailable' : `${(Number(value) * 100).toFixed(0)}%`;
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
  const card = document.createElement('article'); card.className = 'recording'; card.dataset.mentionId = mention.mention_id;
  card.append(text('h3', timestamp(mention.heard_at)), text('p', `${mention.review_status} · ${mention.timing_precision || 'recording'} timing`), text('p', mention.excerpt || 'Mention recorded'), text('p', `Offsets: ${mention.start_offset ?? 'unavailable'}s to ${mention.end_offset ?? 'unavailable'}s`));
  card.append(text('p', `Raw observed: ${mention.raw_observed_value || 'Unavailable'}`), text('p', `Overall confidence: ${percent(mention.confidence)} · Acoustic confidence: ${percent(mention.acoustic_confidence)} · Recognition confidence: ${percent(mention.recognition_confidence)}`), text('p', `Recognition method: ${mention.recognition_method || 'Unavailable'}`));
  if (mention.segment_avg_logprob != null) card.append(text('p', `Whisper segment avg_logprob (raw): ${mention.segment_avg_logprob}`));
  const evidence = text('ul', '', 'saved-evidence'); (mention.evidence || []).forEach(reason => evidence.append(text('li', reason))); card.append(text('h4', 'Saved evidence'), evidence);
  const link = text('a', 'Open recording', 'control-button'); link.href = `/archive/recordings/${encodeURIComponent(mention.recording_id)}`; card.append(link);
  if (mention.audio_available && mention.start_offset != null) { const seek = text('button', 'Play from mention', 'control-button'); seek.type = 'button'; seek.addEventListener('click', () => playMention(seek, mention)); card.append(seek); } else { const disabled = text('button', 'Play from mention', 'control-button'); disabled.disabled = true; card.append(disabled, text('p', `Audio ${mention.audio_status || 'unavailable'}`, 'muted-text')); }
  if (operator()) ['confirm', 'reject', 'correct'].forEach(action => { const button = text('button', action[0].toUpperCase() + action.slice(1), 'control-button'); button.type = 'button'; button.addEventListener('click', () => review(mention, action)); card.append(button); });
  return card;
}

function resetPlaybackButton() {
  if (activePlaybackButton) activePlaybackButton.textContent = 'Play from mention';
  activePlaybackButton = null;
}

function playMention(button, mention) {
  if (activePlaybackButton === button && !player.paused) {
    player.pause();
    resetPlaybackButton();
    return;
  }
  player.pause();
  resetPlaybackButton();
  player.src = `/api/v1/archive/recordings/${encodeURIComponent(mention.recording_id)}/audio`;
  player.onloadedmetadata = () => {
    player.currentTime = Number(mention.start_offset);
    player.play().catch(resetPlaybackButton);
  };
  button.textContent = 'Playing';
  activePlaybackButton = button;
}

player.addEventListener('ended', resetPlaybackButton);

async function loadHistory(reset = false) {
  if (!reset && historyLoading) return;
  const version = ++historyVersion; historyLoading = true; more.disabled = true;
  if (reset) { cursor = null; loadedMentions = new Set(); history.replaceChildren(); }
  try {
    const query = new URLSearchParams({ limit: '50' }); new FormData(filters).forEach((value, key) => { if (value) query.set(key, String(value)); }); if (cursor) query.set('cursor', cursor);
    const response = await fetch(`/api/v1/callsigns/${encodeURIComponent(name)}/mentions?${query}`);
    if (version !== historyVersion) return;
    if (!response.ok) { state.textContent = 'Could not load mention history.'; return; }
    const data = await response.json(); if (version !== historyVersion) return; data.items.forEach(mention => { if (!loadedMentions.has(mention.mention_id)) { loadedMentions.add(mention.mention_id); history.append(renderMention(mention)); } }); cursor = data.next_cursor; more.hidden = !data.has_more; state.textContent = history.children.length ? '' : 'No mention history matches these filters.';
  } catch (_) { if (version === historyVersion) state.textContent = 'Could not load mention history.'; }
  finally { if (version === historyVersion) { historyLoading = false; more.disabled = false; } }
}

async function loadProfile() {
  const response = await fetch(`/api/v1/callsigns/${encodeURIComponent(name)}`); if (!response.ok) { document.querySelector('#profile-location').textContent = 'Callsign not found.'; return; }
  const item = await response.json(); document.querySelector('#profile-callsign').textContent = item.callsign; document.querySelector('#profile-name').textContent = item.qrz_display_name || 'QRZ name unavailable'; document.querySelector('#profile-location').textContent = item.qrz_location || 'QRZ location unavailable'; profile.replaceChildren();
  if (safeExternalUrl(item.qrz_image_url)) { const image = document.createElement('img'); image.className = 'callsign-profile-image'; image.src = safeExternalUrl(item.qrz_image_url); image.alt = `QRZ profile image for ${item.callsign}`; image.referrerPolicy = 'no-referrer'; profile.append(image); }
  if (safeExternalUrl(item.qrz_profile_url)) { const qrz = text('a', 'View QRZ profile', 'callsign-evidence'); qrz.href = safeExternalUrl(item.qrz_profile_url); qrz.target = '_blank'; qrz.rel = 'noopener noreferrer'; profile.append(qrz); }
  profile.append(text('p', `First heard: ${timestamp(item.first_heard)} · Last heard: ${timestamp(item.last_heard)}`), text('p', `${item.total_mentions} mentions · ${item.unique_recordings} recordings · ${item.active_days} active days`), text('p', `Detected: ${item.detected_mentions} · Confirmed: ${item.confirmed_mentions} · Corrected: ${item.corrected_mentions} · Rejected: ${item.rejected_mentions}`), text('p', `Explicitly attributed transmissions: ${item.attributed_transmission_count} · Airtime: ${item.attributed_airtime_seconds}s`), text('p', item.attribution_complete ? 'Attribution coverage is complete.' : 'Attribution coverage is unavailable or incomplete.'));
  const confidence = item.confidence_summary || {}; profile.append(text('p', `Overall mention confidence: ${percent(confidence.minimum)} to ${percent(confidence.maximum)}, average ${percent(confidence.average)}`));
  if (operator()) { const refresh = text('button', 'Refresh QRZ'); refresh.type = 'button'; refresh.addEventListener('click', async () => { const reply = await fetch(`/ui/callsigns/${encodeURIComponent(name)}/qrz-refresh`, { method: 'POST', headers: { 'X-CSRF-Token': csrfToken } }); if (reply.ok) loadProfile(); else state.textContent = 'Could not refresh QRZ profile.'; }); profile.append(refresh); }
}

filters.addEventListener('submit', event => { event.preventDefault(); loadHistory(true); }); more.addEventListener('click', () => loadHistory()); loadProfile(); loadHistory(true);
