const recordings = document.querySelector('#recordings');
const activity = document.querySelector('#activity');
const searchInput = document.querySelector('#search-input');

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function renderJobs(items) {
  const counts = items.reduce((result, item) => {
    result[item.status] = (result[item.status] || 0) + 1;
    return result;
  }, {});
  document.querySelector('#total-count').textContent = items.length;
  document.querySelector('#completed-count').textContent = counts.completed || 0;
  document.querySelector('#processing-count').textContent = counts.processing || 0;
  document.querySelector('#pending-count').textContent = (counts.pending || 0) + (counts.waiting || 0);
  if (!items.length) {
    recordings.innerHTML = '<div class="empty">No recordings match this search.</div>';
    return;
  }
  recordings.innerHTML = items.map(item => `
    <article class="recording">
      <div class="recording-meta"><span class="recording-path">${esc(item.source_path)}</span><span class="recording-date">${item.timestamp ? esc(new Date(item.timestamp).toLocaleString()) : 'Timestamp unavailable'}</span><span class="status ${item.status}">${esc(item.status)}</span></div>
      <button class="play-button" type="button" data-audio-url="${esc(item.audio_url)}" aria-label="Play ${esc(item.source_path)}">▶ Play audio</button>
      <p class="transcript">${item.transcript ? esc(item.transcript.display_text) : '<span style="color:var(--muted)">Awaiting local transcription</span>'}</p>
    </article>`).join('');
  recordings.querySelectorAll('.play-button').forEach(button => button.addEventListener('click', () => playAudio(button)));
}

const player = new Audio();
let activeButton = null;
function playAudio(button) {
  if (activeButton) activeButton.textContent = '▶ Play audio';
  if (activeButton === button && !player.paused) {
    player.pause();
    activeButton = null;
    return;
  }
  player.pause();
  player.src = button.dataset.audioUrl;
  player.play();
  button.textContent = '❚❚ Playing';
  activeButton = button;
}
player.addEventListener('ended', () => { if (activeButton) activeButton.textContent = '▶ Play audio'; activeButton = null; });

async function loadJobs() {
  const query = encodeURIComponent(searchInput.value.trim());
  const response = await fetch(`/api/v1/recordings?limit=500${query ? `&q=${query}` : ''}`);
  if (response.ok) renderJobs((await response.json()).items);
}

async function loadActivity() {
  const response = await fetch('/api/v1/activity', { cache: 'no-store' });
  if (!response.ok) return;
  const data = await response.json();
  document.querySelector('#activity-count').textContent = data.total;
  if (data.total) {
    activity.innerHTML = data.items.slice(-12).reverse().map(item => `
      <div class="activity-item"><time>${esc(new Date(item.timestamp).toLocaleString())}</time><strong>${esc(item.event_type)}</strong>${item.details ? ` <span>${esc(item.details)}</span>` : ''}</div>`).join('');
  }
}

searchInput.addEventListener('input', loadJobs);
loadJobs();
loadActivity();
setInterval(loadActivity, 5000);
const stream = new EventSource('/api/v1/events');
stream.addEventListener('open', () => { document.querySelector('#connection-label').textContent = 'Live archive connection'; });
stream.addEventListener('job', () => { loadJobs(); });
stream.addEventListener('error', () => { document.querySelector('#connection-label').textContent = 'Reconnecting to archive'; });
