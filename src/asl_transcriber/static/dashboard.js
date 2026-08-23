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
  const dot = document.querySelector('#node-status-dot');
  if (!response.ok) {
    state.textContent = response.status === 503 ? 'AMI disabled' : 'Node unavailable';
    state.className = 'status processing';
    dot.className = 'status-dot offline';
    return;
  }
  const data = await response.json();
  state.textContent = data.ami_connected ? 'AMI connected' : 'Node unavailable';
  state.className = data.ami_connected ? 'status' : 'status processing';
  dot.className = `status-dot ${data.talkers.length ? 'talking' : data.ami_connected ? 'idle' : 'offline'}`;
  document.querySelector('#connected-nodes').textContent = data.connected_nodes.length ? data.connected_nodes.join(', ') : 'None';
  document.querySelector('#talkers').textContent = data.talkers.length ? data.talkers.join(', ') : 'None detected';
  document.querySelector('#active-channels').textContent = data.active_channels.length;
  document.querySelector('#stations-count').textContent = data.connected_stations.length;
  document.querySelector('#stations').innerHTML = data.connected_stations.length
    ? data.connected_stations.map(station => {
        const talking = data.talkers.includes(station.id);
        return `<tr class="station-row${talking ? ' talking' : ''}"><td><span class="status-dot ${talking ? 'talking' : 'idle'}"></span></td><td><strong>${esc(station.id)}</strong></td><td>${esc(station.name)}</td><td>${esc(station.state)} · ${esc(station.channel)}</td><td><button class="station-action" data-target="${esc(station.id)}" type="button">Disconnect</button></td></tr>`;
      }).join('')
    : '<tr><td colspan="5" class="empty">No connected stations.</td></tr>';
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
  document.querySelector('#command-buttons').innerHTML = data.commands.map(command => `<option value="${esc(command.name)}" data-requires-target="${command.requires_target}">${esc(command.name)}</option>`).join('');
}
document.querySelector('#control-node-id').addEventListener('change', loadCommands);
document.querySelector('#run-command').addEventListener('click', () => {
  const select = document.querySelector('#command-buttons');
  const option = select.selectedOptions[0];
  if (!option) return;
  const requiresTarget = option.dataset.requiresTarget === 'true';
  const target = requiresTarget ? window.prompt('Target node number') : null;
  if (requiresTarget && !target) return;
  runCommand(option.value, target);
});
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

/* Windows dropdown menu */
const windowsMenuTrigger = document.querySelector('#windows-menu .menu-trigger');
const windowsMenuPanel = document.querySelector('#windows-menu-panel');
windowsMenuTrigger.addEventListener('click', () => {
  const isHidden = windowsMenuPanel.hasAttribute('hidden');
  windowsMenuPanel.toggleAttribute('hidden', !isHidden);
  windowsMenuTrigger.setAttribute('aria-expanded', String(isHidden));
});
document.addEventListener('click', event => {
  if (!event.target.closest('#windows-menu') && !windowsMenuPanel.hasAttribute('hidden')) {
    windowsMenuPanel.setAttribute('hidden', '');
    windowsMenuTrigger.setAttribute('aria-expanded', 'false');
  }
});

/* Settings modal */
const settingsModal = document.querySelector('#settings-modal');
document.querySelector('#open-settings').addEventListener('click', () => settingsModal.removeAttribute('hidden'));
document.querySelector('#close-settings').addEventListener('click', () => settingsModal.setAttribute('hidden', ''));
settingsModal.addEventListener('click', event => { if (event.target === settingsModal) settingsModal.setAttribute('hidden', ''); });

/* Draggable, resizable, collapsible windows with persisted layout */
const desktop = document.querySelector('#desktop');
const LAYOUT_KEY = 'dashboard-layout';
let topZ = 10;

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}'); } catch { return {}; }
}
function saveLayout(layout) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
}
function persistWin(win) {
  const layout = loadLayout();
  layout[win.dataset.win] = {
    top: win.style.top || undefined,
    left: win.style.left || undefined,
    width: win.style.width || undefined,
    height: win.style.height || undefined,
    collapsed: win.classList.contains('collapsed'),
    hidden: win.classList.contains('win-hidden'),
  };
  saveLayout(layout);
}
function bringToFront(win) { win.style.zIndex = String(++topZ); }

document.querySelectorAll('.win').forEach(win => {
  const id = win.dataset.win;
  const saved = loadLayout()[id];
  if (saved) {
    if (saved.top) win.style.top = saved.top;
    if (saved.left) win.style.left = saved.left;
    if (saved.width) win.style.width = saved.width;
    if (saved.height) win.style.height = saved.height;
    if (saved.collapsed) win.classList.add('collapsed');
    if (saved.hidden) win.classList.add('win-hidden');
  }

  const titlebar = win.querySelector('.win-titlebar');
  const collapseButton = win.querySelector('.win-collapse');
  if (win.classList.contains('collapsed')) collapseButton.textContent = '+';

  let dragState = null;
  titlebar.addEventListener('pointerdown', event => {
    if (event.target.closest('button, input, select, label')) return;
    bringToFront(win);
    const desktopRect = desktop.getBoundingClientRect();
    const winRect = win.getBoundingClientRect();
    dragState = {
      startX: event.clientX,
      startY: event.clientY,
      startTop: winRect.top - desktopRect.top + desktop.scrollTop,
      startLeft: winRect.left - desktopRect.left + desktop.scrollLeft,
    };
    titlebar.setPointerCapture(event.pointerId);
  });
  titlebar.addEventListener('pointermove', event => {
    if (!dragState) return;
    const dx = event.clientX - dragState.startX;
    const dy = event.clientY - dragState.startY;
    win.style.left = `${Math.max(0, dragState.startLeft + dx)}px`;
    win.style.top = `${Math.max(0, dragState.startTop + dy)}px`;
  });
  const stopDrag = () => {
    if (!dragState) return;
    dragState = null;
    persistWin(win);
  };
  titlebar.addEventListener('pointerup', stopDrag);
  titlebar.addEventListener('pointercancel', stopDrag);

  win.addEventListener('pointerdown', () => bringToFront(win));

  new ResizeObserver(() => persistWin(win)).observe(win);

  collapseButton.addEventListener('click', () => {
    win.classList.toggle('collapsed');
    collapseButton.textContent = win.classList.contains('collapsed') ? '+' : '–';
    persistWin(win);
  });
});

document.querySelectorAll('#windows-menu-panel input[type="checkbox"]').forEach(checkbox => {
  const win = document.querySelector(`.win[data-win="${checkbox.dataset.toggleWin}"]`);
  if (!win) return;
  checkbox.checked = !win.classList.contains('win-hidden');
  checkbox.addEventListener('change', () => {
    win.classList.toggle('win-hidden', !checkbox.checked);
    persistWin(win);
  });
});

document.querySelector('#reset-layout').addEventListener('click', () => {
  localStorage.removeItem(LAYOUT_KEY);
  window.location.reload();
});
