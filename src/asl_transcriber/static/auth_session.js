/* Shared authentication-loss handling. Loaded first on every signed-in page. */
(() => {
  const nativeFetch = window.fetch.bind(window);
  const NativeEventSource = window.EventSource;
  const channel = 'BroadcastChannel' in window ? new BroadcastChannel('repeater-scribe-auth') : null;
  const streams = new Set();
  let ended = false;
  let checking = null;
  let checkTimer = null;

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function endSession(reason) {
    if (ended) return;
    ended = true;
    streams.forEach(stream => NativeEventSource.prototype.close.call(stream));
    streams.clear();
    const next = reason === 'signed_out' ? '' : `${location.pathname}${location.search}`;
    const params = new URLSearchParams({ reason });
    if (next && next !== '/') params.set('next', next);
    location.replace(`/welcome?${params}`);
  }

  // Only a 401 from the session probe proves the session is gone; outages, 5xx and 403 do not.
  function confirmSession() {
    if (ended) return Promise.resolve();
    checking ||= nativeFetch('/api/v1/auth/me', { cache: 'no-store', credentials: 'same-origin' })
      .then(response => { if (response.status === 401) endSession('expired'); })
      .catch(() => {})
      .finally(() => { checking = null; });
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
})();
