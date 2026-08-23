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

document.querySelector('#run-function').addEventListener('click', async () => {
  const nodeId = document.querySelector('#control-node-id').value.trim();
  const functionInput = document.querySelector('#function-code');
  const code = functionInput.value.trim();
  if (!code) return;
  if (!window.confirm(`Send function ${code} to node ${nodeId}?`)) return;
  const response = await fetch(`/ui/node/${encodeURIComponent(nodeId)}/function`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ function: code }),
  });
  if (response.ok) {
    setControlResult(`Function ${code} sent to node ${nodeId}.`);
    functionInput.value = '';
  } else {
    const detail = await response.json().catch(() => ({}));
    setControlResult(detail.detail || `Function failed (${response.status}).`, true);
  }
});

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

/* Menu tree: top-right button opens a panel with Windows / Layout submenus and a Settings item */
const mainMenuTrigger = document.querySelector('#main-menu-trigger');
const mainMenuPanel = document.querySelector('#main-menu-panel');
mainMenuTrigger.addEventListener('click', () => {
  const isHidden = mainMenuPanel.hasAttribute('hidden');
  mainMenuPanel.toggleAttribute('hidden', !isHidden);
  mainMenuTrigger.setAttribute('aria-expanded', String(isHidden));
  if (!isHidden) closeAllSubmenus();
});
function closeAllSubmenus() {
  document.querySelectorAll('.submenu').forEach(submenu => submenu.setAttribute('hidden', ''));
}
document.querySelectorAll('.menu-item.has-submenu').forEach(item => {
  const submenu = document.querySelector(`#${item.dataset.submenu}`);
  const open = () => {
    const isHidden = submenu.hasAttribute('hidden');
    closeAllSubmenus();
    submenu.toggleAttribute('hidden', !isHidden);
  };
  item.addEventListener('click', open);
  item.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
});
document.addEventListener('click', event => {
  if (!event.target.closest('#main-menu') && !mainMenuPanel.hasAttribute('hidden')) {
    mainMenuPanel.setAttribute('hidden', '');
    mainMenuTrigger.setAttribute('aria-expanded', 'false');
    closeAllSubmenus();
  }
});

/* Settings modal */
const settingsModal = document.querySelector('#settings-modal');
document.querySelector('#open-settings').addEventListener('click', () => {
  settingsModal.removeAttribute('hidden');
  mainMenuPanel.setAttribute('hidden', '');
  closeAllSubmenus();
});
document.querySelector('#close-settings').addEventListener('click', () => settingsModal.setAttribute('hidden', ''));
settingsModal.addEventListener('click', event => { if (event.target === settingsModal) settingsModal.setAttribute('hidden', ''); });

/* Draggable, resizable, collapsible, closable windows with two independent constraint toggles:
   "no overlap" (mode 1's collision avoidance) and "bound to screen" (mode 1's viewport limit).
   Both on = structured mode; both off = freeform mode; either can be toggled independently. */
const desktop = document.querySelector('#desktop');
const LAYOUT_KEY = 'dashboard-layout';
const PRESETS_KEY = 'dashboard-layout-presets';
const CONSTRAINTS_KEY = 'dashboard-constraints';
const DOCK_WIDTH = 340;
const GRID = 26;
let topZ = 10;

const DEFAULT_ORDER = ['queue', 'node', 'stations', 'controls', 'transcripts', 'activity'];
const PANEL_SIZES = {
  queue: { w: 320, h: 210 },
  node: { w: 380, h: 290 },
  stations: { w: 380, h: 290 },
  controls: { w: 420, h: 300 },
  transcripts: { w: 640, h: 480 },
  activity: { w: 360, h: 300 },
};

function snap(value) {
  return Math.round(value / GRID) * GRID;
}

function loadConstraints() {
  try { return { noOverlap: true, bound: true, ...JSON.parse(localStorage.getItem(CONSTRAINTS_KEY) || '{}') }; } catch { return { noOverlap: true, bound: true }; }
}
function saveConstraints() {
  localStorage.setItem(CONSTRAINTS_KEY, JSON.stringify(constraints));
}
let constraints = loadConstraints();

/* Best-fit tiling: lays out currently visible standard windows, in order, wrapped to fit the
   available width, snapped to the grid. Used for the initial layout, resets, and whenever mode 1
   (no overlap + bound to screen) needs to make room for an opened/closed window or a resize. */
function autoFitLayout(maxWidthOverride) {
  const width = Math.max(maxWidthOverride || desktop.clientWidth || window.innerWidth, 320);
  const visible = DEFAULT_ORDER
    .map(id => document.querySelector(`.win[data-win="${id}"]`))
    .filter(win => win && !win.classList.contains('win-hidden'));
  let x = GRID;
  let y = GRID;
  let rowHeight = 0;
  visible.forEach(win => {
    const size = PANEL_SIZES[win.dataset.win];
    const w = Math.min(size.w, Math.max(width - GRID * 2, 260));
    const h = size.h;
    if (x > GRID && x + w + GRID > width) {
      x = GRID;
      y += rowHeight + GRID;
      rowHeight = 0;
    }
    win.style.left = `${snap(x)}px`;
    win.style.top = `${snap(y)}px`;
    win.style.width = `${snap(w)}px`;
    win.style.height = `${snap(h)}px`;
    x += w + GRID;
    rowHeight = Math.max(rowHeight, h);
  });
}

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}'); } catch { return {}; }
}
function saveLayout(layout) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
}
function loadPresets() {
  try { return JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}'); } catch { return {}; }
}
function savePresets(presets) {
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

function snapshotLayout() {
  const snapshot = {};
  document.querySelectorAll('.win').forEach(win => {
    snapshot[win.dataset.win] = {
      top: win.style.top || undefined,
      left: win.style.left || undefined,
      width: win.style.width || undefined,
      height: win.style.height || undefined,
      collapsed: win.classList.contains('collapsed'),
      hidden: win.classList.contains('win-hidden'),
      docked: win.classList.contains('docked'),
    };
  });
  return snapshot;
}

function applyLayout(snapshot) {
  document.querySelectorAll('.win').forEach(win => {
    const saved = snapshot[win.dataset.win];
    if (!saved) return;
    if (saved.top) win.style.top = saved.top;
    if (saved.left) win.style.left = saved.left;
    if (saved.width) win.style.width = saved.width;
    if (saved.height) win.style.height = saved.height;
    win.classList.toggle('collapsed', !!saved.collapsed);
    win.classList.toggle('win-hidden', !!saved.hidden);
    win.classList.toggle('docked', !!saved.docked);
    const collapseButton = win.querySelector('.win-collapse');
    if (collapseButton) collapseButton.textContent = saved.collapsed ? '+' : '–';
  });
  syncWindowMenuCheckboxes();
  saveLayout(snapshot);
}

function persistAll() {
  saveLayout(snapshotLayout());
}

function syncWindowMenuCheckboxes() {
  document.querySelectorAll('#windows-submenu input[type="checkbox"]').forEach(checkbox => {
    const win = document.querySelector(`.win[data-win="${checkbox.dataset.toggleWin}"]`);
    if (win) checkbox.checked = !win.classList.contains('win-hidden');
  });
}

function bringToFront(win) {
  win.style.zIndex = String(++topZ);
  document.querySelectorAll('.win.active').forEach(other => other.classList.remove('active'));
  win.classList.add('active');
}

/* Collision + bounds helpers, applied only when the matching constraint is enabled */
function rectOf(win) {
  return { left: parseFloat(win.style.left) || 0, top: parseFloat(win.style.top) || 0, width: win.offsetWidth, height: win.offsetHeight };
}
function overlaps(a, b) {
  return a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top;
}
function visibleWindows(exclude) {
  return Array.from(document.querySelectorAll('.win')).filter(win => win !== exclude && !win.classList.contains('win-hidden'));
}
function collidesAt(win, left, top) {
  const rect = { left, top, width: win.offsetWidth, height: win.offsetHeight };
  return visibleWindows(win).some(other => overlaps(rect, rectOf(other)));
}
function clampToBounds(left, top, width, height) {
  const maxLeft = Math.max(0, desktop.clientWidth - width);
  const maxTop = Math.max(0, desktop.clientHeight - height);
  return { left: Math.min(Math.max(left, 0), maxLeft), top: Math.min(Math.max(top, 0), maxTop) };
}

const lastGoodRect = new WeakMap();
function captureGoodRect(win) {
  if (!win.classList.contains('win-hidden')) lastGoodRect.set(win, rectOf(win));
}

/* Properties window docks to the right edge and cannot be moved or resized */
function dockProperties(show) {
  const win = document.querySelector('.win[data-win="properties"]');
  if (!win) return;
  if (show) {
    win.classList.remove('win-hidden');
    win.classList.add('docked');
    const width = Math.min(DOCK_WIDTH, Math.max(240, desktop.clientWidth - GRID * 2));
    const left = Math.max(GRID, desktop.clientWidth - width - GRID);
    win.style.left = `${left}px`;
    win.style.top = `${GRID}px`;
    win.style.width = `${width}px`;
    win.style.height = `${Math.max(200, desktop.clientHeight - GRID * 2)}px`;
    if (constraints.noOverlap && constraints.bound) autoFitLayout(left - GRID);
    bringToFront(win);
  } else {
    win.classList.add('win-hidden');
    win.classList.remove('collapsed');
    win.classList.remove('docked');
    if (constraints.noOverlap && constraints.bound) autoFitLayout();
  }
  syncWindowMenuCheckboxes();
  persistAll();
}

/* Showing/hiding a standard window; mode 1 (both constraints on) re-fits everything to make room */
function setWindowVisible(win, visible) {
  win.classList.toggle('win-hidden', !visible);
  if (!visible) win.classList.remove('collapsed');
  else captureGoodRect(win);
  if (constraints.noOverlap && constraints.bound) autoFitLayout();
  syncWindowMenuCheckboxes();
  persistAll();
}

const savedLayout = loadLayout();
if (!Object.keys(savedLayout).length) autoFitLayout();

document.querySelectorAll('.win').forEach(win => {
  const id = win.dataset.win;
  const saved = savedLayout[id];
  if (saved) {
    if (saved.top) win.style.top = saved.top;
    if (saved.left) win.style.left = saved.left;
    if (saved.width) win.style.width = saved.width;
    if (saved.height) win.style.height = saved.height;
    if (saved.collapsed) win.classList.add('collapsed');
    if (saved.hidden) win.classList.add('win-hidden');
    if (saved.docked) win.classList.add('docked');
  }
  captureGoodRect(win);

  const titlebar = win.querySelector('.win-titlebar');
  const collapseButton = win.querySelector('.win-collapse');
  const closeButton = win.querySelector('.win-close');
  if (win.classList.contains('collapsed')) collapseButton.textContent = '+';

  let dragState = null;
  titlebar.addEventListener('pointerdown', event => {
    if (win.classList.contains('docked')) return;
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
    let left = dragState.startLeft + dx;
    let top = dragState.startTop + dy;
    if (constraints.noOverlap) { left = snap(left); top = snap(top); }
    if (constraints.bound) {
      const clamped = clampToBounds(left, top, win.offsetWidth, win.offsetHeight);
      left = clamped.left;
      top = clamped.top;
    } else {
      top = Math.max(0, top);
    }
    if (constraints.noOverlap && collidesAt(win, left, top)) {
      const originalLeft = parseFloat(win.style.left) || 0;
      const originalTop = parseFloat(win.style.top) || 0;
      if (!collidesAt(win, left, originalTop)) top = originalTop;
      else if (!collidesAt(win, originalLeft, top)) left = originalLeft;
      else { left = originalLeft; top = originalTop; }
    }
    win.style.left = `${Math.max(0, left)}px`;
    win.style.top = `${Math.max(0, top)}px`;
  });
  const stopDrag = () => {
    if (!dragState) return;
    dragState = null;
    captureGoodRect(win);
    persistAll();
  };
  titlebar.addEventListener('pointerup', stopDrag);
  titlebar.addEventListener('pointercancel', stopDrag);

  win.addEventListener('pointerdown', () => bringToFront(win));

  new ResizeObserver(() => {
    if (win.classList.contains('docked')) return;
    let rect = rectOf(win);
    if (constraints.bound) {
      const maxWidth = desktop.clientWidth - rect.left;
      const maxHeight = desktop.clientHeight - rect.top;
      if (rect.width > maxWidth) { win.style.width = `${Math.max(220, maxWidth)}px`; }
      if (rect.height > maxHeight) { win.style.height = `${Math.max(140, maxHeight)}px`; }
      rect = rectOf(win);
    }
    if (constraints.noOverlap && collidesAt(win, rect.left, rect.top)) {
      const good = lastGoodRect.get(win);
      if (good) {
        win.style.width = `${good.width}px`;
        win.style.height = `${good.height}px`;
      }
    } else {
      captureGoodRect(win);
    }
    persistAll();
  }).observe(win);

  collapseButton.addEventListener('click', () => {
    win.classList.toggle('collapsed');
    collapseButton.textContent = win.classList.contains('collapsed') ? '+' : '–';
    persistAll();
  });

  if (closeButton) {
    closeButton.addEventListener('click', () => {
      if (id === 'properties') dockProperties(false);
      else setWindowVisible(win, false);
    });
  }
});

document.querySelectorAll('#windows-submenu input[type="checkbox"]').forEach(checkbox => {
  const win = document.querySelector(`.win[data-win="${checkbox.dataset.toggleWin}"]`);
  if (!win) return;
  checkbox.checked = !win.classList.contains('win-hidden');
  checkbox.addEventListener('change', () => {
    if (checkbox.dataset.toggleWin === 'properties') {
      dockProperties(checkbox.checked);
      return;
    }
    setWindowVisible(win, checkbox.checked);
  });
});

/* Display mode: two independent constraints, plus Structured/Freeform quick-select buttons */
function applyConstraintsUI() {
  document.querySelector('#constraint-no-overlap').checked = constraints.noOverlap;
  document.querySelector('#constraint-bound').checked = constraints.bound;
}
document.querySelector('#constraint-no-overlap').addEventListener('change', event => {
  constraints.noOverlap = event.target.checked;
  saveConstraints();
});
document.querySelector('#constraint-bound').addEventListener('change', event => {
  constraints.bound = event.target.checked;
  saveConstraints();
});
document.querySelector('#mode-structured').addEventListener('click', () => {
  constraints = { noOverlap: true, bound: true };
  saveConstraints();
  applyConstraintsUI();
  autoFitLayout();
  persistAll();
});
document.querySelector('#mode-freeform').addEventListener('click', () => {
  constraints = { noOverlap: false, bound: false };
  saveConstraints();
  applyConstraintsUI();
});
applyConstraintsUI();

/* Layout presets: save, apply, and remove named window arrangements */
function renderPresetList() {
  const presets = loadPresets();
  const container = document.querySelector('#layout-preset-list');
  const names = Object.keys(presets);
  if (!names.length) {
    container.innerHTML = '<p class="empty-note">No saved presets</p>';
    return;
  }
  container.innerHTML = names.map(name => `
    <div class="preset-row">
      <button class="preset-name" type="button" data-preset="${esc(name)}">${esc(name)}</button>
      <button class="preset-delete" type="button" data-remove-preset="${esc(name)}" aria-label="Delete preset ${esc(name)}">&times;</button>
    </div>`).join('');
  container.querySelectorAll('.preset-name').forEach(button => button.addEventListener('click', () => {
    const preset = loadPresets()[button.dataset.preset];
    if (preset) applyLayout(preset);
  }));
  container.querySelectorAll('.preset-delete').forEach(button => button.addEventListener('click', () => {
    const presets = loadPresets();
    delete presets[button.dataset.removePreset];
    savePresets(presets);
    renderPresetList();
  }));
}
document.querySelector('#layout-save').addEventListener('click', () => {
  const name = window.prompt('Name this layout preset');
  if (!name || !name.trim()) return;
  const presets = loadPresets();
  presets[name.trim()] = snapshotLayout();
  savePresets(presets);
  renderPresetList();
});
document.querySelector('#reset-layout').addEventListener('click', () => {
  localStorage.removeItem(LAYOUT_KEY);
  dockProperties(false);
  autoFitLayout();
  persistAll();
});
renderPresetList();

let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const propertiesWin = document.querySelector('.win[data-win="properties"]');
    const dockedOpen = propertiesWin && propertiesWin.classList.contains('docked') && !propertiesWin.classList.contains('win-hidden');
    if (dockedOpen) {
      dockProperties(true);
    } else if (constraints.noOverlap && constraints.bound) {
      autoFitLayout();
    } else if (constraints.bound) {
      visibleWindows().forEach(win => {
        if (win.classList.contains('docked')) return;
        const rect = rectOf(win);
        const clamped = clampToBounds(rect.left, rect.top, rect.width, rect.height);
        win.style.left = `${clamped.left}px`;
        win.style.top = `${clamped.top}px`;
      });
    }
    persistAll();
  }, 200);
});


