/* Personal API token controls; raw credentials live only in the one-time reveal. */
(() => {
  const role = document.body.dataset.role;
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const feedback = document.querySelector('#settings-feedback');
  const options = [
    { name: 'api:read', label: 'Read API data', help: 'Transcripts, audio, activity, callsigns, Events, and permitted reads.', roles: ['viewer', 'user', 'admin'], checked: true },
    { name: 'content:write', label: 'Edit shared content', help: 'Events, check-ins, tags, and transcript corrections.', roles: ['user', 'admin'] },
    { name: 'node:control', label: 'Manage Favorites and node controls', help: 'Still subject to AMI and runtime control settings.', roles: ['user', 'admin'] },
    { name: 'admin', label: 'Administrative API access', help: 'Account, ingestion, and diagnostic endpoints.', roles: ['admin'] },
  ];
  const panel = document.createElement('section');
  let list;
  let createButton;
  let creating = false;
  let loadVersion = 0;

  function node(tag, text = '', className = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  async function request(url, init = {}) {
    const headers = new Headers(init.headers || {});
    headers.set('Accept', 'application/json');
    headers.set('X-CSRF-Token', csrfToken);
    if (init.body) headers.set('Content-Type', 'application/json');
    const response = await fetch(url, { ...init, headers, credentials: 'same-origin' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `Request failed (${response.status})`);
    return result;
  }

  function localDate(value) {
    if (!value) return 'Never';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? 'Unknown' : new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(parsed);
  }

  function showSecret(result) {
    const reveal = node('section', '', 'token-reveal');
    reveal.setAttribute('role', 'status');
    reveal.append(node('h4', 'Copy this token now'), node('p', 'The complete secret will not be shown again. Store it securely before leaving this page.'));
    const secret = node('textarea', '', 'token-secret');
    secret.readOnly = true;
    secret.rows = 3;
    secret.value = result.token;
    secret.setAttribute('aria-label', 'New personal API token');
    const copy = node('button', 'Copy token', 'control-button primary');
    copy.type = 'button';
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(secret.value);
        feedback.textContent = 'Token copied to clipboard.';
      } catch {
        secret.focus();
        secret.select();
        feedback.textContent = 'Select and copy the token securely.';
      }
    });
    reveal.append(secret, copy);
    panel.replaceChildren(reveal);
    panel.hidden = false;
  }

  function renderTokens(tokens) {
    list.replaceChildren();
    if (!tokens.length) {
      list.append(node('p', 'No personal API tokens.'));
      return;
    }
    for (const token of tokens) {
      const item = node('article', '', 'personal-token-item');
      const heading = document.createElement('header');
      heading.append(node('strong', token.name), node('span', token.status, `token-status token-status-${token.status}`));
      item.append(heading, node('p', token.prefix || 'Secret unavailable'), node('p', `Scopes: ${token.scopes.join(', ') || 'None'}`));
      item.append(node('small', `Created ${localDate(token.created_at)} · Expires ${localDate(token.expires_at)} · Last used ${localDate(token.last_used_at)}`));
      if (token.status === 'active') {
        const revoke = node('button', 'Revoke token', 'control-button danger');
        revoke.type = 'button';
        revoke.addEventListener('click', async () => {
          if (!window.confirm(`Revoke ${token.name}? Applications using it will lose API access immediately.`)) return;
          revoke.disabled = true;
          try {
            await request(`/api/v1/personal-tokens/${encodeURIComponent(token.id)}`, { method: 'DELETE' });
            feedback.textContent = `Revoked ${token.name}.`;
            await loadTokens();
          } catch (error) {
            feedback.textContent = error.message;
            revoke.disabled = false;
          }
        });
        item.append(revoke);
      }
      list.append(item);
    }
  }

  async function loadTokens() {
    const version = ++loadVersion;
    feedback.textContent = 'Loading personal tokens...';
    try {
      const result = await request('/api/v1/personal-tokens');
      if (version !== loadVersion) return;
      renderTokens(result.items);
      feedback.textContent = `${result.items.length} personal token${result.items.length === 1 ? '' : 's'}.`;
    } catch (error) {
      if (version === loadVersion) feedback.textContent = error.message;
    }
  }

  window.RuntimeSettings.registerPage({
    id: 'api-access',
    label: 'API Access',
    scopeText: 'Personal tokens inherit your current account role and the scopes you grant.',
    available: () => Boolean(document.body.dataset.accountId),
    mount(settingsPanel) {
      panel.className = 'personal-token-settings';
      panel.hidden = true;
      const form = document.createElement('form');
      form.className = 'personal-token-form';
      form.setAttribute('aria-label', 'Create personal API token');

      const nameLabel = node('label', 'Token name');
      const name = document.createElement('input');
      name.name = 'name';
      name.required = true;
      name.maxLength = 128;
      name.autocomplete = 'off';
      nameLabel.append(name);

      const expiryLabel = node('label', 'Expires in');
      const expiry = document.createElement('select');
      expiry.name = 'expires_in_days';
      for (const days of [30, 90, 365]) {
        const option = node('option', `${days} days`);
        option.value = String(days);
        if (days === 90) option.selected = true;
        expiry.append(option);
      }
      expiryLabel.append(expiry);

      const presetLabel = node('label', 'Permission preset');
      const preset = document.createElement('select');
      preset.name = 'preset';
      const presets = {
        read: ['api:read'],
        content: ['api:read', 'content:write'],
        node: ['api:read', 'node:control'],
        admin: ['api:read', 'content:write', 'node:control', 'admin'],
        custom: null,
      };
      const availablePresets = [
        ['read', 'Read only'],
        ...(role !== 'viewer' ? [['content', 'Shared content'], ['node', 'Node operations']] : []),
        ...(role === 'admin' ? [['admin', 'Administrator']] : []),
        ['custom', 'Custom scopes'],
      ];
      for (const [value, label] of availablePresets) {
        const option = node('option', label);
        option.value = value;
        if (value === 'read') option.selected = true;
        preset.append(option);
      }
      presetLabel.append(preset);

      const scopeSet = document.createElement('fieldset');
      scopeSet.className = 'token-scope-options';
      scopeSet.append(node('legend', 'Permissions'));
      const selectedScopes = new Map();
      for (const option of options.filter(item => item.roles.includes(role))) {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.name = 'scopes';
        checkbox.value = option.name;
        checkbox.checked = Boolean(option.checked);
        selectedScopes.set(option.name, checkbox);
        label.append(checkbox, node('strong', option.label), node('small', option.help));
        scopeSet.append(label);
      }
      const adminWarning = node('p', 'Admin scope grants administrative API access. Grant it only to trusted automation.');
      adminWarning.className = 'token-admin-warning';
      adminWarning.hidden = true;
      scopeSet.append(adminWarning);
      preset.addEventListener('change', () => {
        const selected = presets[preset.value];
        if (!selected) return;
        for (const [scope, checkbox] of selectedScopes) {
          checkbox.checked = selected.includes(scope);
        }
        adminWarning.hidden = !selected.includes('admin');
      });
      for (const checkbox of selectedScopes.values()) {
        checkbox.addEventListener('change', () => { preset.value = 'custom'; });
      }
      selectedScopes.get('admin')?.addEventListener('change', () => {
        adminWarning.hidden = !selectedScopes.get('admin').checked;
      });

      createButton = node('button', 'Create token', 'control-button primary');
      createButton.type = 'submit';
      form.append(nameLabel, expiryLabel, presetLabel, scopeSet, createButton);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (creating) return;
        const scopes = [...selectedScopes].filter(([, checkbox]) => checkbox.checked).map(([scope]) => scope);
        if (!scopes.length) {
          feedback.textContent = 'Choose at least one permission.';
          return;
        }
        if (scopes.includes('admin') && !window.confirm('This token can access administrative APIs. Create it only for trusted automation. Continue?')) return;
        creating = true;
        createButton.disabled = true;
        feedback.textContent = 'Creating personal token...';
        try {
          const result = await request('/api/v1/personal-tokens', {
            method: 'POST',
            body: JSON.stringify({ name: name.value, expires_in_days: Number(expiry.value), scopes }),
          });
          showSecret(result);
          name.value = '';
          await loadTokens();
        } catch (error) {
          feedback.textContent = error.message;
        } finally {
          creating = false;
          createButton.disabled = false;
        }
      });

      list = node('div', '', 'personal-token-list');
      const docsLink = node('a', 'Open API Console', 'control-button');
      docsLink.href = '/docs';
      docsLink.target = '_blank';
      docsLink.rel = 'noopener noreferrer';
      settingsPanel.append(
        panel,
        node('p', 'To rotate a credential, create its replacement, copy the new secret, then revoke the old token.'),
        docsLink,
        form,
        list,
      );
      loadTokens();
    },
  });
})();