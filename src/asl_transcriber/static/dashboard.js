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

/* VS Code / AvalonDock-style window manager:
   - Docked panels live in a tree of splits (row/column) and tab groups; the tree structure
     guarantees they never overlap and never leave the dock area, no per-window math needed.
   - Any panel can be undocked into a floating window (absolute position, draggable, resizable),
     always clamped to the visible browser viewport since we can't spawn real OS windows.
   - Docking, undocking, retabbing, splitting, and resizing all mutate a single `state` tree that
     is re-rendered and persisted after every change. */
const desktop = document.querySelector('#desktop');
const dockRoot = document.querySelector('#dock-root');
const floatLayer = document.querySelector('#float-layer');
const STATE_KEY = 'dashboard-dock-state';
const PRESETS_KEY = 'dashboard-layout-presets';
let topZ = 10;
let groupSeq = 0;

const PANEL_TITLES = {
  queue: 'Queue summary',
  node: 'Node status',
  stations: 'Connected stations',
  controls: 'Node controls',
  transcripts: 'Transcripts',
  activity: 'Activity log',
  properties: 'Properties',
};
const ALL_PANELS = Object.keys(PANEL_TITLES);

function makeGroup(...panels) {
  return { type: 'group', id: `group-${groupSeq++}`, panels: [...panels], active: panels[0] };
}

function defaultTree() {
  groupSeq = 0;
  return {
    type: 'split', direction: 'column', sizes: [0.36, 0.64],
    children: [
      { type: 'split', direction: 'row', sizes: [0.22, 0.45, 0.33], children: [
        makeGroup('queue'), makeGroup('node'), makeGroup('controls'),
      ] },
      { type: 'split', direction: 'row', sizes: [0.6, 0.4], children: [
        makeGroup('transcripts'),
        { type: 'split', direction: 'column', sizes: [0.5, 0.5], children: [makeGroup('stations'), makeGroup('activity')] },
      ] },
    ],
  };
}

function loadState() {
  try {
    const raw = JSON.parse(localStorage.getItem(STATE_KEY));
    if (raw && raw.tree) {
      const maxId = JSON.stringify(raw.tree).match(/group-(\d+)/g) || [];
      groupSeq = maxId.reduce((max, id) => Math.max(max, Number(id.split('-')[1]) + 1), 0);
      return raw;
    }
  } catch { /* fall through to default */ }
  return { tree: defaultTree(), floating: {}, hidden: ['properties'] };
}
function persist() {
  localStorage.setItem(STATE_KEY, JSON.stringify(state));
}
function loadPresets() {
  try { return JSON.parse(localStorage.getItem(PRESETS_KEY) || '{}'); } catch { return {}; }
}
function savePresets(presets) {
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

let state = loadState();

/* Tree helpers */
function findGroupWithPanel(node, panelId) {
  if (!node) return null;
  if (node.type === 'group') return node.panels.includes(panelId) ? node : null;
  for (const child of node.children) {
    const found = findGroupWithPanel(child, panelId);
    if (found) return found;
  }
  return null;
}
function findGroupById(node, groupId) {
  if (!node) return null;
  if (node.type === 'group') return node.id === groupId ? node : null;
  for (const child of node.children) {
    const found = findGroupById(child, groupId);
    if (found) return found;
  }
  return null;
}
function normalizeSizes(sizes) {
  const total = sizes.reduce((sum, size) => sum + size, 0) || 1;
  return sizes.map(size => size / total);
}
function removePanelFromTree(node, panelId) {
  if (!node) return null;
  if (node.type === 'group') {
    node.panels = node.panels.filter(panel => panel !== panelId);
    if (!node.panels.length) return null;
    if (node.active === panelId) node.active = node.panels[0];
    return node;
  }
  const survivors = [];
  const survivorSizes = [];
  node.children.forEach((child, index) => {
    const result = removePanelFromTree(child, panelId);
    if (result) { survivors.push(result); survivorSizes.push(node.sizes[index]); }
  });
  if (!survivors.length) return null;
  if (survivors.length === 1) return survivors[0];
  node.children = survivors;
  node.sizes = normalizeSizes(survivorSizes);
  return node;
}
function replaceInTree(node, targetId, replacement) {
  if (node.type === 'group') return node.id === targetId ? replacement : node;
  node.children = node.children.map(child => replaceInTree(child, targetId, replacement));
  return node;
}
function dockPanelDefault(panelId) {
  const newGroup = makeGroup(panelId);
  if (!state.tree) { state.tree = newGroup; return; }
  if (state.tree.type === 'split' && state.tree.direction === 'row') {
    state.tree.children.push(newGroup);
    state.tree.sizes = normalizeSizes([...state.tree.sizes, 1]);
  } else {
    state.tree = { type: 'split', direction: 'row', sizes: [0.7, 0.3], children: [state.tree, newGroup] };
  }
}
function floatingCount() {
  return Object.keys(state.floating).length;
}
function clampFloatRect(rect) {
  const width = Math.min(Math.max(rect.width, 260), Math.max(desktop.clientWidth - 32, 260));
  const height = Math.min(Math.max(rect.height, 160), Math.max(desktop.clientHeight - 32, 160));
  const left = Math.min(Math.max(rect.left, 0), Math.max(desktop.clientWidth - width, 0));
  const top = Math.min(Math.max(rect.top, 0), Math.max(desktop.clientHeight - height, 0));
  return { left, top, width, height };
}

/* Panel visibility actions */
function undockPanel(panelId) {
  state.tree = removePanelFromTree(state.tree, panelId);
  state.floating[panelId] = { ...clampFloatRect({ left: 60 + floatingCount() * 28, top: 60 + floatingCount() * 28, width: 420, height: 320 }), collapsed: false };
  renderAll();
  persist();
}
function dockFloatingPanel(panelId) {
  delete state.floating[panelId];
  dockPanelDefault(panelId);
  renderAll();
  persist();
}
function closePanel(panelId) {
  state.tree = removePanelFromTree(state.tree, panelId);
  delete state.floating[panelId];
  if (!state.hidden.includes(panelId)) state.hidden.push(panelId);
  const win = document.querySelector(`.win[data-win="${panelId}"]`);
  win.classList.remove('floating');
  win.style.display = 'none';
  floatLayer.appendChild(win);
  renderAll();
  persist();
}
function setPanelVisible(panelId, visible) {
  if (visible) {
    state.hidden = state.hidden.filter(id => id !== panelId);
    if (!findGroupWithPanel(state.tree, panelId) && !state.floating[panelId]) dockPanelDefault(panelId);
  } else {
    closePanel(panelId);
    return;
  }
  renderAll();
  persist();
}
const DROP_CLASSES = ['drop-center', 'drop-left', 'drop-right', 'drop-top', 'drop-bottom'];
let activeDrop = null;

function clearDockTargets() {
  document.querySelectorAll('.dock-group').forEach(group => group.classList.remove(...DROP_CLASSES));
  activeDrop = null;
}

function updateDockTarget(clientX, clientY, panelId) {
  clearDockTargets();
  const groupEl = document.elementsFromPoint(clientX, clientY)
    .map(element => element.closest('.dock-group'))
    .find(Boolean);
  if (!groupEl) return null;

  const targetGroup = findGroupById(state.tree, groupEl.dataset.groupId);
  const sourceGroup = findGroupWithPanel(state.tree, panelId);
  if (!targetGroup || (sourceGroup?.id === targetGroup.id && sourceGroup.panels.length === 1)) return null;

  const rect = groupEl.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  const edgeX = Math.min(72, rect.width * 0.24);
  const edgeY = Math.min(72, rect.height * 0.24);
  let zone = null;
  if (y <= 42) zone = 'center';
  else if (x <= edgeX) zone = 'left';
  else if (x >= rect.width - edgeX) zone = 'right';
  else if (y <= edgeY) zone = 'top';
  else if (y >= rect.height - edgeY) zone = 'bottom';
  if (!zone) return null;

  groupEl.classList.add(`drop-${zone}`);
  activeDrop = { groupId: targetGroup.id, zone };
  return activeDrop;
}

function dockPanelAt(panelId, targetGroupId, zone) {
  const sourceGroup = findGroupWithPanel(state.tree, panelId);
  if (sourceGroup?.id === targetGroupId && zone === 'center') return;

  state.tree = removePanelFromTree(state.tree, panelId);
  delete state.floating[panelId];
  state.hidden = state.hidden.filter(id => id !== panelId);
  const targetGroup = findGroupById(state.tree, targetGroupId);
  if (!targetGroup) {
    dockPanelDefault(panelId);
  } else if (zone === 'center') {
    targetGroup.panels.push(panelId);
    targetGroup.active = panelId;
  } else {
    const incoming = makeGroup(panelId);
    const direction = zone === 'left' || zone === 'right' ? 'row' : 'column';
    const incomingFirst = zone === 'left' || zone === 'top';
    const replacement = {
      type: 'split',
      direction,
      sizes: [0.5, 0.5],
      children: incomingFirst ? [incoming, targetGroup] : [targetGroup, incoming],
    };
    state.tree = replaceInTree(state.tree, targetGroupId, replacement);
  }
  clearDockTargets();
  renderAll();
  persist();
}

function syncWindowMenuCheckboxes() {
  document.querySelectorAll('#windows-submenu input[type="checkbox"]').forEach(checkbox => {
    const panelId = checkbox.dataset.toggleWin;
    checkbox.checked = !state.hidden.includes(panelId);
  });
}

function bringToFront(win) {
  win.style.zIndex = String(++topZ);
  document.querySelectorAll('.win.active').forEach(other => other.classList.remove('active'));
  win.classList.add('active');
}

/* Rendering: rebuild the dock tree DOM and reposition floating windows */
function renderAll() {
  ALL_PANELS.forEach(panelId => {
    const win = document.querySelector(`.win[data-win="${panelId}"]`);
    if (win) document.body.appendChild(win);
  });
  dockRoot.innerHTML = '';
  if (state.tree) dockRoot.appendChild(renderNode(state.tree));
  ALL_PANELS.forEach(panelId => {
    const win = document.querySelector(`.win[data-win="${panelId}"]`);
    if (state.floating[panelId]) {
      const rect = state.floating[panelId];
      const collapseButton = win.querySelector('.win-collapse');
      const dockButton = win.querySelector('.win-dock');
      win.classList.add('floating');
      win.classList.toggle('collapsed', !!rect.collapsed);
      if (collapseButton) collapseButton.textContent = rect.collapsed ? '+' : '–';
      if (dockButton) {
        dockButton.innerHTML = '&#8600;';
        dockButton.setAttribute('aria-label', 'Dock window');
        dockButton.title = 'Dock window';
      }
      win.style.left = `${rect.left}px`;
      win.style.top = `${rect.top}px`;
      win.style.width = `${rect.width}px`;
      win.style.height = `${rect.height}px`;
      win.style.display = '';
      floatLayer.appendChild(win);
    } else if (state.hidden.includes(panelId) && !findGroupWithPanel(state.tree, panelId)) {
      win.classList.remove('floating', 'collapsed');
      win.style.display = 'none';
      floatLayer.appendChild(win);
    }
  });
  syncWindowMenuCheckboxes();
}

function renderNode(node) {
  if (node.type === 'group') return renderGroup(node);
  const container = document.createElement('div');
  container.className = `dock-split dock-${node.direction}`;
  node.children.forEach((child, index) => {
    const pane = document.createElement('div');
    pane.className = 'dock-pane';
    pane.style.flexBasis = `${(node.sizes[index] * 100).toFixed(4)}%`;
    pane.appendChild(renderNode(child));
    container.appendChild(pane);
    if (index < node.children.length - 1) {
      const splitter = document.createElement('div');
      splitter.className = `dock-splitter dock-splitter-${node.direction}`;
      attachSplitterDrag(splitter, node, index, container);
      container.appendChild(splitter);
    }
  });
  return container;
}

function renderGroup(node) {
  const wrap = document.createElement('div');
  wrap.className = `dock-group${node.panels.length === 1 ? ' singleton' : ''}`;
  wrap.dataset.groupId = node.id;

  if (node.panels.length > 1) {
    const tabstrip = document.createElement('div');
    tabstrip.className = 'dock-tabstrip';
    node.panels.forEach(panelId => {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = `dock-tab${panelId === node.active ? ' active' : ''}`;
      tab.textContent = PANEL_TITLES[panelId];
      tab.dataset.panel = panelId;
      tab.addEventListener('click', () => { node.active = panelId; renderAll(); persist(); });
      attachTabDrag(tab, panelId, node);
      tabstrip.appendChild(tab);
    });

    const actions = document.createElement('div');
    actions.className = 'dock-group-actions';
    actions.appendChild(iconButton('&#8599;', 'Undock', () => undockPanel(node.active)));
    const closeButton = iconButton('&times;', 'Close', () => closePanel(node.active));
    closeButton.classList.add('dock-close');
    actions.appendChild(closeButton);
    tabstrip.appendChild(actions);
    wrap.appendChild(tabstrip);
  }

  const body = document.createElement('div');
  body.className = 'dock-body';
  node.panels.forEach(panelId => {
    const win = document.querySelector(`.win[data-win="${panelId}"]`);
    win.classList.remove('floating', 'collapsed');
    win.style.cssText = '';
    win.style.display = panelId === node.active ? '' : 'none';
    const dockButton = win.querySelector('.win-dock');
    if (dockButton) {
      dockButton.innerHTML = '&#8599;';
      dockButton.setAttribute('aria-label', 'Undock window');
      dockButton.title = 'Undock window';
    }
    body.appendChild(win);
  });
  wrap.appendChild(body);
  return wrap;
}

function iconButton(html, label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.innerHTML = html;
  button.setAttribute('aria-label', label);
  button.title = label;
  button.addEventListener('click', onClick);
  return button;
}

/* Splitters resize the two adjacent panes; sizes are stored as fractions of their combined share */
function attachSplitterDrag(splitter, node, index, container) {
  let dragging = null;
  splitter.addEventListener('pointerdown', event => {
    const panes = Array.from(container.children).filter(el => el.classList.contains('dock-pane'));
    const a = panes[index];
    const b = panes[index + 1];
    dragging = {
      startX: event.clientX,
      startY: event.clientY,
      aSize: node.direction === 'row' ? a.getBoundingClientRect().width : a.getBoundingClientRect().height,
      bSize: node.direction === 'row' ? b.getBoundingClientRect().width : b.getBoundingClientRect().height,
      a, b,
    };
    splitter.setPointerCapture(event.pointerId);
  });
  splitter.addEventListener('pointermove', event => {
    if (!dragging) return;
    const delta = node.direction === 'row' ? event.clientX - dragging.startX : event.clientY - dragging.startY;
    const total = dragging.aSize + dragging.bSize;
    let aPx = Math.min(Math.max(dragging.aSize + delta, 120), total - 120);
    const bPx = total - aPx;
    const pairTotal = node.sizes[index] + node.sizes[index + 1];
    node.sizes[index] = (aPx / total) * pairTotal;
    node.sizes[index + 1] = pairTotal - node.sizes[index];
    dragging.a.style.flexBasis = `${(node.sizes[index] * 100).toFixed(4)}%`;
    dragging.b.style.flexBasis = `${(node.sizes[index + 1] * 100).toFixed(4)}%`;
  });
  const stop = () => { if (dragging) { dragging = null; persist(); } };
  splitter.addEventListener('pointerup', stop);
  splitter.addEventListener('pointercancel', stop);
}

/* Dragging a tab: title/header drops create tabs; highlighted edge drops split only that pane. */
function attachTabDrag(tabEl, panelId, sourceGroup) {
  let dragging = false;
  tabEl.addEventListener('pointerdown', event => {
    dragging = true;
    tabEl.setPointerCapture(event.pointerId);
  });
  tabEl.addEventListener('pointermove', event => {
    if (!dragging) return;
    updateDockTarget(event.clientX, event.clientY, panelId);
  });
  tabEl.addEventListener('pointerup', event => {
    if (!dragging) return;
    dragging = false;
    const drop = updateDockTarget(event.clientX, event.clientY, panelId);
    if (drop) {
      dockPanelAt(panelId, drop.groupId, drop.zone);
      return;
    }
    clearDockTargets();
    const dockRect = dockRoot.getBoundingClientRect();
    const outsideDock = event.clientX < dockRect.left || event.clientX > dockRect.right
      || event.clientY < dockRect.top || event.clientY > dockRect.bottom;
    if (outsideDock) {
      const desktopRect = desktop.getBoundingClientRect();
      state.tree = removePanelFromTree(state.tree, panelId);
      state.floating[panelId] = {
        ...clampFloatRect({ left: event.clientX - desktopRect.left - 60, top: event.clientY - desktopRect.top - 16, width: 420, height: 320 }),
        collapsed: false,
      };
      renderAll();
      persist();
    }
  });
}

/* Floating window chrome: drag by title bar, resize via native corner handle, dock/close/collapse */
ALL_PANELS.forEach(panelId => {
  const win = document.querySelector(`.win[data-win="${panelId}"]`);
  const titlebar = win.querySelector('.win-titlebar');
  const collapseButton = win.querySelector('.win-collapse');
  const dockButton = win.querySelector('.win-dock');
  const closeButton = win.querySelector('.win-close');

  let dragState = null;
  let dockDragState = false;
  titlebar.addEventListener('pointerdown', event => {
    if (event.target.closest('button, input, select, label')) return;
    if (!win.classList.contains('floating')) {
      if (!titlebar.closest('.dock-group.singleton')) return;
      dockDragState = true;
      titlebar.setPointerCapture(event.pointerId);
      return;
    }
    bringToFront(win);
    const desktopRect = desktop.getBoundingClientRect();
    const winRect = win.getBoundingClientRect();
    dragState = {
      startX: event.clientX,
      startY: event.clientY,
      startTop: winRect.top - desktopRect.top,
      startLeft: winRect.left - desktopRect.left,
    };
    titlebar.setPointerCapture(event.pointerId);
  });
  titlebar.addEventListener('pointermove', event => {
    if (dockDragState) {
      updateDockTarget(event.clientX, event.clientY, panelId);
      return;
    }
    if (!dragState) return;
    const dx = event.clientX - dragState.startX;
    const dy = event.clientY - dragState.startY;
    const rect = clampFloatRect({ left: dragState.startLeft + dx, top: dragState.startTop + dy, width: win.offsetWidth, height: win.offsetHeight });
    win.style.left = `${rect.left}px`;
    win.style.top = `${rect.top}px`;
    state.floating[panelId] = { ...state.floating[panelId], ...rect };
    updateDockTarget(event.clientX, event.clientY, panelId);
  });
  titlebar.addEventListener('pointerup', event => {
    if (!dragState && !dockDragState) return;
    const drop = updateDockTarget(event.clientX, event.clientY, panelId);
    dragState = null;
    dockDragState = false;
    if (drop) dockPanelAt(panelId, drop.groupId, drop.zone);
    else { clearDockTargets(); persist(); }
  });
  titlebar.addEventListener('pointercancel', () => {
    dragState = null;
    dockDragState = false;
    clearDockTargets();
  });

  win.addEventListener('pointerdown', () => { if (win.classList.contains('floating')) bringToFront(win); });

  new ResizeObserver(() => {
    if (!win.classList.contains('floating') || win.classList.contains('collapsed')) return;
    const clamped = clampFloatRect({ left: parseFloat(win.style.left) || 0, top: parseFloat(win.style.top) || 0, width: win.offsetWidth, height: win.offsetHeight });
    win.style.width = `${clamped.width}px`;
    win.style.height = `${clamped.height}px`;
    state.floating[panelId] = { ...state.floating[panelId], ...clamped };
    persist();
  }).observe(win);

  collapseButton.addEventListener('click', () => {
    win.classList.toggle('collapsed');
    collapseButton.textContent = win.classList.contains('collapsed') ? '+' : '–';
    if (state.floating[panelId]) {
      state.floating[panelId].collapsed = win.classList.contains('collapsed');
      persist();
    }
  });
  dockButton.addEventListener('click', () => {
    if (win.classList.contains('floating')) dockFloatingPanel(panelId);
    else undockPanel(panelId);
  });
  closeButton.addEventListener('click', () => closePanel(panelId));
});

document.querySelectorAll('#windows-submenu input[type="checkbox"]').forEach(checkbox => {
  checkbox.addEventListener('change', () => setPanelVisible(checkbox.dataset.toggleWin, checkbox.checked));
});

/* Layout presets: save, apply, and remove named dock/float arrangements */
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
    if (preset) {
      state = JSON.parse(JSON.stringify(preset));
      renderAll();
      persist();
    }
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
  presets[name.trim()] = JSON.parse(JSON.stringify(state));
  savePresets(presets);
  renderPresetList();
});
document.querySelector('#reset-layout').addEventListener('click', () => {
  state = { tree: defaultTree(), floating: {}, hidden: ['properties'] };
  renderAll();
  persist();
});
renderPresetList();

renderAll();

let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    let changed = false;
    Object.keys(state.floating).forEach(panelId => {
      const clamped = clampFloatRect(state.floating[panelId]);
      if (clamped.left !== state.floating[panelId].left || clamped.top !== state.floating[panelId].top
        || clamped.width !== state.floating[panelId].width || clamped.height !== state.floating[panelId].height) {
        state.floating[panelId] = clamped;
        changed = true;
      }
    });
    if (changed) { renderAll(); persist(); }
  }, 200);
});

