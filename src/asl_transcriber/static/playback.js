// One playback owner per page, shared by native controls and custom players.
window.Playback = (() => {
  const players = new Map();
  let active = null;

  function cancel(audio) {
    const state = players.get(audio);
    if (state) {
      state.version++;
      state.cancelSeek?.();
      state.cancelSeek = null;
    }
    if (active === audio) active = null;
    audio.pause();
  }

  function select(audio) {
    for (const other of players.keys()) if (other !== audio) cancel(other);
    active = audio;
  }

  function register(audio, { persistent = false } = {}) {
    if (players.has(audio)) return audio;
    const state = { version: 0, persistent, cancelSeek: null };
    const onPlay = () => {
      // A queued play event from a cancelled load must not reclaim ownership.
      if (audio.paused) return;
      if (!persistent && !audio.isConnected) { cancel(audio); return; }
      select(audio);
    };
    const onPlaying = () => { if (active !== audio) cancel(audio); };
    const onPause = () => { if (audio.paused) cancel(audio); };
    const onTerminal = () => {
      if (audio.ended || audio.error) cancel(audio);
    };
    const listeners = { play: onPlay, playing: onPlaying, pause: onPause, ended: onTerminal, error: onTerminal };
    for (const [type, listener] of Object.entries(listeners)) audio.addEventListener(type, listener);
    state.listeners = listeners;
    players.set(audio, state);
    return audio;
  }

  function remove(audio) {
    const state = players.get(audio);
    if (!state) return;
    cancel(audio);
    for (const [type, listener] of Object.entries(state.listeners)) audio.removeEventListener(type, listener);
    players.delete(audio);
  }

  async function play(audio, { src, offset } = {}) {
    const state = players.get(audio);
    if (!state || (!state.persistent && !audio.isConnected)) return;
    select(audio);
    state.cancelSeek?.();
    state.cancelSeek = null;
    const version = ++state.version;
    const current = () => players.get(audio) === state && state.version === version && active === audio;
    if (src && audio.src !== new URL(src, location.href).href) {
      audio.pause();
      audio.src = src;
    }
    if (offset != null) {
      const seek = () => { if (current()) audio.currentTime = Number(offset) || 0; };
      if (audio.readyState) seek();
      else {
        audio.addEventListener('loadedmetadata', seek, { once: true });
        state.cancelSeek = () => audio.removeEventListener('loadedmetadata', seek);
      }
    }
    try {
      // Start now; metadata handlers only seek and can never restart old playback.
      await audio.play();
      if (active !== audio) audio.pause();
    } catch (error) {
      if (current()) cancel(audio);
      throw error;
    }
  }

  // Check after a DOM batch so moving retained players during refresh is harmless.
  new MutationObserver(() => {
    for (const [audio, state] of players) if (!state.persistent && !audio.isConnected) remove(audio);
  }).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener('pagehide', () => { for (const audio of players.keys()) cancel(audio); });
  return { register, play, pause: cancel, remove };
})();
