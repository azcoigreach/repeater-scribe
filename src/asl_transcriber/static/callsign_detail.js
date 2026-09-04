const name = window.callsignName;
const profile = document.querySelector('#profile');
const history = document.querySelector('#history');
function text(tag, value, className = '') { const node = document.createElement(tag); node.textContent = value ?? ''; if (className) node.className = className; return node; }
async function load() {
  const response = await fetch(`/api/v1/callsigns/${encodeURIComponent(name)}`); if (!response.ok) { document.querySelector('#profile-location').textContent = 'Callsign not found.'; return; }
  const item = await response.json(); document.querySelector('#profile-location').textContent = item.qrz_location || 'QRZ location unavailable';
  profile.append(text('p', `First heard: ${item.first_heard || 'Unavailable'} · Last heard: ${item.last_heard || 'Unavailable'}`), text('p', `${item.total_mentions} non-rejected mentions across ${item.unique_recordings} recordings on ${item.active_days} active days`), text('p', `Detected: ${item.detected_mentions} · Confirmed: ${item.confirmed_mentions} · Corrected: ${item.corrected_mentions} · Rejected: ${item.rejected_mentions}`));
  const mentions = await fetch(`/api/v1/callsigns/${encodeURIComponent(name)}/mentions`); if (mentions.ok) (await mentions.json()).items.forEach(mention => { const card = document.createElement('article'); card.className = 'recording'; card.append(text('time', mention.heard_at || 'Time unavailable'), text('p', mention.excerpt || 'Mention recorded'), text('p', `Offsets: ${mention.start_offset ?? 'unavailable'}s to ${mention.end_offset ?? 'unavailable'}s · ${mention.review_status}`)); const link = text('a', 'Open recording'); link.href = mention.recording_url; card.append(link); if (mention.audio_available && mention.start_offset != null) { const seek = text('button', 'Play from mention'); seek.type = 'button'; seek.addEventListener('click', async () => { const audio = new Audio(`/api/v1/archive/recordings/${encodeURIComponent(mention.recording_id)}/audio`); audio.addEventListener('loadedmetadata', () => { audio.currentTime = Number(mention.start_offset); audio.play().catch(() => {}); }, { once: true }); }); card.append(seek); } history.append(card); });
  document.querySelector('#history-state').textContent = history.children.length ? '' : 'No mention history available.';
}
load();
