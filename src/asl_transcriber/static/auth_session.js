/* Shared authentication-loss handling. Loaded first on every signed-in page. */
(() => {
  const nativeFetch = window.fetch.bind(window);
  const NativeEventSource = window.EventSource;
  const channel = 'BroadcastChannel' in window ? new BroadcastChannel('repeater-scribe-auth') : null;
  const streams = new Set();
  const SESSION_CHECK_INTERVAL_MS = 60_000;
  const SESSION_PROBE_TIMEOUT_MS = 5_000;
  let ended = false;
  let checking = null;
  let checkTimer = null;

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function endSession(reason) {
    if (ended) return;
    ended = true;
    deactivateWorkspace();
    const next = reason === 'signed_out' ? '' : `${location.pathname}${location.search}`;
    const params = new URLSearchParams({ reason });
    if (next && next !== '/') params.set('next', next);
    location.replace(`/welcome?${params}`);
  }

  function deactivateWorkspace() {
    clearInterval(sessionCheckInterval);
    clearTimeout(checkTimer);
    streams.forEach(stream => NativeEventSource.prototype.close.call(stream));
    streams.clear();
    window.Playback?.stopAll();
    if (document.body) {
      document.body.inert = true;
      document.body.setAttribute('aria-busy', 'true');
    }
  }

  function refreshWorkspace() {
    if (ended) return;
    ended = true;
    deactivateWorkspace();
    location.reload();
  }

  // This read-only endpoint does not extend the idle timeout; outages never prove logout.
  async function checkSession() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), SESSION_PROBE_TIMEOUT_MS);
    try {
      const response = await nativeFetch('/api/v1/auth/me', {
        cache: 'no-store', credentials: 'same-origin', signal: controller.signal,
      });
      if (response.status === 401) {
        endSession('expired');
      } else if (response.ok) {
        const principal = await response.json();
        if (principal.auth_source === 'session' && principal.csrf_token !== csrfToken()) {
          refreshWorkspace();
        }
      }
    } catch (_) {
      // A timeout or transport failure is transient, not proof of logout.
    } finally {
      clearTimeout(timeout);
    }
  }

  function confirmSession() {
    if (ended) return Promise.resolve();
    checking ||= checkSession().finally(() => { checking = null; });
    return checking;
  }

  function scheduleCheck() {
    if (ended || checkTimer !== null) return;
    checkTimer = setTimeout(() => { checkTimer = null; confirmSession(); }, 500);
  }

  // Once signed out, late responses must never reach UI code that would repaint live state.
  window.fetch = async (input, init) => {
    if (ended) return new Promise(() => {});
    const response = await nativeFetch(input, init);
    if (ended) return new Promise(() => {});
    if (response.status === 401) {
      const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
      if (url.origin === location.origin && !url.pathname.startsWith('/auth/') && url.pathname !== '/api/v1/auth/me') scheduleCheck();
    }
    return response;
  };

  if (NativeEventSource) {
    window.EventSource = class extends NativeEventSource {
      constructor(url, options) {
        super(url, options);
        if (ended) { super.close(); return; }
        streams.add(this);
        this.addEventListener('error', scheduleCheck);
      }
      close() { streams.delete(this); super.close(); }
    };
  }

  async function signOut() {
    try {
      const response = await nativeFetch('/auth/logout', {
        method: 'POST', headers: { 'X-CSRF-Token': csrfToken() }, redirect: 'manual', credentials: 'same-origin',
      });
      // A manual redirect surfaces as an opaque response; 401 means the session had already ended.
      if (!(response.type === 'opaqueredirect' || response.ok || response.status === 401)) throw new Error('logout failed');
    } catch (_) {
      window.alert('Sign out failed. Check your connection and try again.');
      return;
    }
    channel?.postMessage('signed-out');
    endSession('signed_out');
  }

  channel?.addEventListener('message', event => { if (event.data === 'signed-out') endSession('signed_out'); });
  document.addEventListener('click', event => {
    if (event.target.closest('#sign-out')) { event.preventDefault(); signOut(); }
  });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') confirmSession(); });
  window.addEventListener('focus', confirmSession);
  window.addEventListener('online', confirmSession);
  window.addEventListener('pageshow', event => { if (event.persisted) confirmSession(); });
  const sessionCheckInterval = setInterval(() => {
    if (document.visibilityState === 'visible') confirmSession();
  }, SESSION_CHECK_INTERVAL_MS);
})();
