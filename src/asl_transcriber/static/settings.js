/* Runtime presentation preferences and shared, accessible Settings page shell. */
window.RuntimeSettings = (() => {
  const modal = document.querySelector('#settings-modal');
  const tabs = document.querySelector('#settings-tabs');
  const panels = document.querySelector('#settings-pages');
  const feedback = document.querySelector('#settings-feedback');
  const actions = document.querySelector('#settings-personal-actions');
  const storageKey = `repeater-scribe:preferences:v1:${document.body.dataset.preferenceScope}`;
  const definitions = new Map();
  const pages = new Map();
  const values = {};
  const inputs = new Map();
  let saved = {};
  let returnFocus;
  let activePage;
  try {
    const parsed = JSON.parse(localStorage.getItem(storageKey));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed;
  } catch { /* Missing, obsolete or inaccessible storage uses effective defaults. */ }

  function valid(definition, value) {
    return definition.choices ? definition.choices.includes(value)
      : Number.isInteger(value) && value >= definition.min && value <= definition.max;
  }
  function registerSetting(key, definition) {
    if (definitions.has(key) || !valid(definition, definition.default)) throw new Error('Invalid setting definition');
    definitions.set(key, definition);
    values[key] = valid(definition, saved[key]) ? saved[key] : definition.default;
  }
  function selectPage(id, focus = true) {
    activePage = id;
    for (const [key, page] of pages) {
      const selected = key === id;
      page.tab.setAttribute('aria-selected', String(selected));
      page.tab.tabIndex = selected ? 0 : -1;
      page.panel.hidden = !selected;
    }
    // Later account/token pages own their actions. These controls only edit personal fields.
    actions.hidden = !pages.get(id).personal;
    feedback.textContent = '';
    if (focus) pages.get(id).tab.focus();
  }
  function registerPage({ id, label, settings = [], personal = false, available = () => true, mount }) {
    if (!available()) return;
    if (pages.has(id)) throw new Error('Duplicate Settings page');
    const tab = document.createElement('button');
    tab.id = `settings-tab-${id}`;
    tab.type = 'button';
    tab.textContent = label;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', `settings-page-${id}`);
    tab.addEventListener('click', () => selectPage(id));
    const panel = document.createElement('section');
    panel.id = `settings-page-${id}`;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    panel.tabIndex = 0;
    const title = document.createElement('h3');
    title.textContent = label;
    panel.append(title);
    for (const key of settings) {
      const definition = definitions.get(key);
      const field = document.createElement('div');
      field.className = 'settings-field';
      const label = document.createElement('label');
      const input = document.createElement('input');
      const help = document.createElement('p');
      input.id = `setting-${key.replaceAll('.', '-')}`;
      input.type = 'number';
      input.min = definition.min;
      input.max = definition.max;
      input.step = '1';
      input.required = true;
      label.htmlFor = input.id;
      label.textContent = definition.label;
      help.id = `${input.id}-help`;
      help.textContent = `${definition.help} Range: ${definition.min}–${definition.max}. Default: ${definition.default}.`;
      input.setAttribute('aria-describedby', help.id);
      input.addEventListener('input', () => input.removeAttribute('aria-invalid'));
      inputs.set(key, { input, page: id });
      field.append(label, input, help);
      panel.append(field);
    }
    if (mount) mount(panel);
    pages.set(id, { tab, panel, personal });
    tabs.append(tab);
    panels.append(panel);
    selectPage(activePage || id, false);
  }
  tabs.addEventListener('keydown', event => {
    const ids = [...pages.keys()];
    const index = ids.indexOf(activePage);
    const next = { ArrowDown: (index + 1) % ids.length, ArrowUp: (index - 1 + ids.length) % ids.length, Home: 0, End: ids.length - 1 }[event.key];
    if (next !== undefined) { event.preventDefault(); selectPage(ids[next]); }
  });
  modal.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const focusable = [...modal.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')]
      .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });
  function resetDraft(defaults = false) {
    for (const [key, { input }] of inputs) {
      input.value = defaults ? definitions.get(key).default : values[key];
      input.removeAttribute('aria-invalid');
    }
    feedback.textContent = '';
  }
  function close() { modal.close(); }
  modal.addEventListener('close', () => {
    modal.hidden = true;
    resetDraft();
    returnFocus?.focus();
  });
  modal.addEventListener('click', event => { if (event.target === modal) close(); });
  document.querySelector('#close-settings').addEventListener('click', close);
  document.querySelector('#settings-cancel').addEventListener('click', close);
  document.querySelector('#settings-defaults').addEventListener('click', () => {
    resetDraft(true);
    feedback.textContent = 'Defaults are ready to review. Apply to save them, or Cancel to keep your preferences.';
  });
  document.querySelector('#settings-apply').addEventListener('click', () => {
    const next = { ...values };
    for (const [key, { input, page }] of inputs) {
      const definition = definitions.get(key);
      const value = input.value === '' ? NaN : Number(input.value);
      if (!valid(definition, value)) {
        selectPage(page, false);
        input.setAttribute('aria-invalid', 'true');
        feedback.textContent = `${definition.label}: enter a whole number from ${definition.min} to ${definition.max}.`;
        input.focus();
        return;
      }
      next[key] = value;
    }
    try { localStorage.setItem(storageKey, JSON.stringify(next)); }
    catch {
      feedback.textContent = 'Preferences could not be saved. Allow browser storage and try Apply again.';
      return;
    }
    const changed = Object.keys(next).filter(key => next[key] !== values[key]);
    Object.assign(values, next);
    window.dispatchEvent(new CustomEvent('runtime-settings-change', { detail: changed }));
    feedback.textContent = 'Preferences applied.';
  });

  registerSetting('dashboard.transcriptLimit', {
    default: 500, min: 1, max: 500, label: 'Transcript display count',
    help: 'Maximum recordings shown in the Transcripts window, including search results. Archive history is retained.',
  });
  const serverDefault = Number(document.body.dataset.lastHeardDefault);
  registerSetting('dashboard.lastHeardLimit', {
    default: Number.isInteger(serverDefault) && serverDefault >= 1 && serverDefault <= 100 ? serverDefault : 25,
    min: 1, max: 100, label: 'Last Heard station display count',
    help: 'Maximum unique eligible stations shown in Last Heard. QRZ lookup budgets still apply.',
  });
  registerSetting('appearance.theme', { default: 'operator-dark', choices: ['operator-dark'] });
  registerPage({ id: 'dashboard', label: 'Dashboard', personal: true,
    settings: ['dashboard.transcriptLimit', 'dashboard.lastHeardLimit'] });
  registerPage({ id: 'appearance', label: 'Appearance', personal: true, mount(panel) {
    const description = document.createElement('p');
    description.textContent = 'Current theme: Dark operator. Additional themes are not available yet.';
    panel.append(description);
  } });
  return Object.freeze({
    get: key => values[key], registerSetting, registerPage,
    open(opener) {
      returnFocus = opener;
      resetDraft();
      modal.hidden = false;
      modal.showModal();
      selectPage(activePage);
    },
  });
})();
