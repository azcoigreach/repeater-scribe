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

async function loadNodeStatus() {
  const response = await fetch('/api/v1/node/status', { cache: 'no-store' });
  const state = document.querySelector('#node-state');
  if (!response.ok) {
    state.textContent = response.status === 503 ? 'AMI disabled' : 'Node unavailable';
    state.className = 'status processing';
    return;
  }
  const data = await response.json();
  state.textContent = data.ami_connected ? 'AMI connected' : 'Node unavailable';
  state.className = data.ami_connected ? 'status' : 'status processing';
  document.querySelector('#connected-nodes').textContent = data.connected_nodes.length ? data.connected_nodes.join(', ') : 'None';
  document.querySelector('#talkers').textContent = data.talkers.length ? data.talkers.join(', ') : 'None detected';
  document.querySelector('#active-channels').textContent = data.active_channels.length;
  document.querySelector('#stations').innerHTML = data.connected_stations.length
    ? data.connected_stations.map(station => `<div class="station"><div><strong>${esc(station.id)}</strong><span>${esc(station.name)}</span></div><small>${esc(station.state)} · ${esc(station.channel)}</small><button class="station-action" data-target="${esc(station.id)}" type="button">Disconnect</button></div>`).join('')
    : '<div class="empty">No connected stations.</div>';
  document.querySelectorAll('.station-action').forEach(button => button.addEventListener('click', () => runCommand('Disconnect node', button.dataset.target)));
}

function setControlResult(message, error = false) {
  const result = document.querySelector('#control-result');
  result.textContent = message;
  result.className = `control-result${error ? ' error' : ''}`;
}

document.querySelector('#refresh-node').addEventListener('click', loadNodeStatus);
document.querySelector('#ping-node').addEventListener('click', async () => {
  const response = await fetch('/api/v1/node/ping', { method: 'POST' });
  setControlResult(response.ok ? 'Node responded to AMI ping.' : `Ping failed (${response.status}).`, !response.ok);
});
async function loadCommands() {
  const nodeId = document.querySelector('#control-node-id').value.trim();
  const response = await fetch(`/api/v1/node/${encodeURIComponent(nodeId)}/commands`);
  if (!response.ok) return;
  const data = await response.json();
  document.querySelector('#command-buttons').innerHTML = data.commands.map(command => `<button class="command-button" type="button" data-command="${esc(command.name)}" data-requires-target="${command.requires_target}">${esc(command.name)}</button>`).join('');
  document.querySelectorAll('.command-button').forEach(button => button.addEventListener('click', () => {
    const target = button.dataset.requiresTarget === 'true' ? window.prompt('Target node number') : null;
    if (button.dataset.requiresTarget === 'true' && !target) return;
    runCommand(button.dataset.command, target);
  }));
}
document.querySelector('#control-node-id').addEventListener('change', loadCommands);
async function runCommand(name, target = null) {
  const nodeId = document.querySelector('#control-node-id').value.trim();
  if (!window.confirm(`${name}${target ? ` ${target}` : ''}?`)) return;
  const response = await fetch(`/ui/node/${encodeURIComponent(nodeId)}/command`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, target, confirmed: document.querySelector('#command-confirm').checked }),
  });
  if (response.ok) {
    setControlResult(`Command ${name} sent to node ${nodeId}.`);
    document.querySelector('#command-confirm').checked = false;
  } else {
    const detail = await response.json().catch(() => ({}));
    setControlResult(detail.detail || `Command failed (${response.status}).`, true);
  }
}
loadCommands();

document.querySelectorAll('.nav-button').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.nav-button, .view').forEach(item => item.classList.remove('active', 'active-view'));
  button.classList.add('active');
  document.querySelector(`#${button.dataset.view}`).classList.add('active-view');
}));
searchInput.addEventListener('input', loadJobs);
loadJobs();
loadActivity();
setInterval(loadActivity, 5000);
loadNodeStatus();
setInterval(loadNodeStatus, 5000);
const stream = new EventSource('/api/v1/events');
stream.addEventListener('open', () => { document.querySelector('#connection-label').textContent = 'Live archive connection'; });
stream.addEventListener('job', () => { loadJobs(); });
stream.addEventListener('error', () => { document.querySelector('#connection-label').textContent = 'Reconnecting to archive'; });
