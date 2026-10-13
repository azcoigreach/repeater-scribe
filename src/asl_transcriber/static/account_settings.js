/* Admin account management inside the shared Settings shell. */
(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const feedback = document.querySelector('#settings-feedback');
  const accountsBody = document.createElement('tbody');
  const busyAccounts = new Set();
  let accounts = [];
  let requestVersion = 0;
  let provisioning = false;
  let loadingAccounts = false;
  let moreButton;

  function node(tag, text = '', className = '') {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    headers.set('X-CSRF-Token', csrfToken);
    if (options.body) headers.set('Content-Type', 'application/json');
    const response = await fetch(url, { ...options, headers });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || `Request failed (${response.status})`);
    return result;
  }

  function date(value) {
    if (!value) return 'Never';
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? 'Unknown' : new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: 'short',
    }).format(parsed);
  }

  function displayName(account) {
    return account.display_name || account.preferred_username || account.email || account.identity;
  }

  function roleName(role) {
    return ({ admin: 'Admin', user: 'User', viewer: 'Viewer' })[role] || role;
  }

  function actionButton(label, handler, disabled = false) {
    const button = node('button', label, 'control-button');
    button.type = 'button';
    button.disabled = disabled;
    button.addEventListener('click', handler);
    return button;
  }

  function renderAccounts() {
    accountsBody.replaceChildren();
    for (const account of accounts) {
      const row = document.createElement('tr');
      const identity = displayName(account);
      const identityCell = document.createElement('td');
      identityCell.append(node('strong', identity), node('small', account.email || account.preferred_username || ''));
      identityCell.append(node('small', `${account.issuer} / ${account.subject}`, 'account-provider-id'));

      const roleCell = document.createElement('td');
      const roleSelect = document.createElement('select');
      roleSelect.setAttribute('aria-label', `Role for ${identity}`);
      for (const [value, label] of [['viewer', 'Viewer'], ['user', 'User'], ['admin', 'Admin']]) {
        const option = node('option', label);
        option.value = value;
        roleSelect.append(option);
      }
      roleSelect.value = account.role;
      roleSelect.disabled = busyAccounts.has(account.id);
      roleCell.append(roleSelect, actionButton('Change role', async () => {
        if (roleSelect.value === account.role) {
          feedback.textContent = 'Choose a different role before saving.';
          roleSelect.focus();
          return;
        }
        if (!window.confirm(`Change ${identity}'s role from ${roleName(account.role)} to ${roleName(roleSelect.value)}?`)) return;
        await mutate(account, { role: roleSelect.value }, `Role updated for ${identity}.`);
      }, busyAccounts.has(account.id)));

      const stateCell = document.createElement('td');
      stateCell.append(node('strong', !account.enabled ? 'Disabled' : account.provisioned_pending ? 'Provisioned' : 'Enabled'));
      if (account.provisioned_pending) stateCell.append(node('small', 'No successful sign-in yet'));

      const firstSignIn = node('td', date(account.first_sign_in_at));
      const lastSignIn = node('td', date(account.last_sign_in_at));
      const sessions = node('td', String(account.active_session_count));

      const actionsCell = document.createElement('td');
      const actions = node('div', '', 'account-row-actions');
      actions.append(actionButton(account.enabled ? 'Disable account' : 'Re-enable account', async () => {
        const enabled = !account.enabled;
        const confirmation = enabled
          ? `Re-enable ${identity}? A new sign-in is required; revoked sessions will not be restored.`
          : `Disable ${identity}? This blocks sign-in and revokes all browser sessions.`;
        if (!window.confirm(confirmation)) return;
        await mutate(account, { enabled }, `${enabled ? 'Account enabled' : 'Account disabled'} for ${identity}.`);
      }, busyAccounts.has(account.id)));
      actions.append(actionButton('Revoke all sessions', async () => {
        if (!window.confirm(`Revoke every browser session for ${identity}? This affects all devices.`)) return;
        try {
          busyAccounts.add(account.id);
          renderAccounts();
          const result = await request(`/api/v1/accounts/${encodeURIComponent(account.id)}/sessions/revoke`, { method: 'POST' });
          await loadAccounts(`Revoked ${result.revoked_sessions} browser sessions for ${identity}.`);
        } catch (error) {
          feedback.textContent = error.message;
        } finally {
          busyAccounts.delete(account.id);
          renderAccounts();
        }
      }, busyAccounts.has(account.id) || account.active_session_count === 0));
      actionsCell.append(actions);
      row.append(identityCell, roleCell, stateCell, firstSignIn, lastSignIn, sessions, actionsCell);
      accountsBody.append(row);
    }
  }

  async function loadAccounts(message = '', append = false) {
    if (loadingAccounts) return;
    loadingAccounts = true;
    const version = ++requestVersion;
    const offset = append ? accounts.length : 0;
    feedback.textContent = 'Loading accounts...';
    try {
      const result = await request(`/api/v1/accounts?limit=200&offset=${offset}`);
      if (version !== requestVersion) return;
      accounts = append ? accounts.concat(result.items) : result.items;
      renderAccounts();
      moreButton.hidden = result.items.length < 200;
      feedback.textContent = message || `${accounts.length} accounts loaded.`;
    } catch (error) {
      if (version === requestVersion) feedback.textContent = error.message;
    } finally {
      if (version === requestVersion) {
        loadingAccounts = false;
        moreButton.disabled = false;
      }
    }
  }

  async function mutate(account, change, message) {
    if (busyAccounts.has(account.id)) return;
    busyAccounts.add(account.id);
    renderAccounts();
    try {
      await request(`/api/v1/accounts/${encodeURIComponent(account.id)}`, {
        method: 'PATCH', body: JSON.stringify(change),
      });
      await loadAccounts(message);
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      busyAccounts.delete(account.id);
      renderAccounts();
    }
  }

  window.RuntimeSettings.registerPage({ id: 'users', label: 'Users', personal: false,
    available: () => document.body.dataset.role === 'admin', mount(panel) {
      const form = document.createElement('form');
      form.className = 'account-provision-form';
      form.setAttribute('aria-label', 'Provision OIDC identity');
      const subjectLabel = node('label', 'OIDC subject');
      const subject = document.createElement('input');
      subject.name = 'subject';
      subject.required = true;
      subject.maxLength = 255;
      subject.autocomplete = 'off';
      subjectLabel.append(subject);
      const roleLabel = node('label', 'Initial role');
      const role = document.createElement('select');
      role.name = 'role';
      for (const [value, label] of [['viewer', 'Viewer'], ['user', 'User'], ['admin', 'Admin']]) {
        const option = node('option', label);
        option.value = value;
        role.append(option);
      }
      roleLabel.append(role);
      const submit = node('button', 'Provision account', 'control-button primary');
      submit.type = 'submit';
      form.append(subjectLabel, roleLabel, submit);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (provisioning) return;
        provisioning = true;
        submit.disabled = true;
        try {
          const result = await request('/api/v1/accounts', {
            method: 'POST', body: JSON.stringify({ subject: subject.value, role: role.value }),
          });
          form.reset();
          await loadAccounts(`Provisioned ${result.identity}; sign-in is still pending.`);
        } catch (error) {
          feedback.textContent = error.message;
        } finally {
          provisioning = false;
          submit.disabled = false;
        }
      });

      const tableWrap = node('div', '', 'account-table-wrap');
      const table = document.createElement('table');
      table.className = 'account-table';
      const head = document.createElement('thead');
      const headingRow = document.createElement('tr');
      for (const label of ['Identity', 'Role', 'Access', 'First sign-in', 'Last sign-in', 'Sessions', 'Actions']) {
        headingRow.append(node('th', label));
      }
      head.append(headingRow);
      table.append(head, accountsBody);
      tableWrap.append(table);
      moreButton = node('button', 'Load more accounts', 'control-button account-more');
      moreButton.type = 'button';
      moreButton.hidden = true;
      moreButton.addEventListener('click', () => {
        moreButton.disabled = true;
        loadAccounts('', true);
      });
      panel.append(form, tableWrap, moreButton);
      loadAccounts();
    } });
})();