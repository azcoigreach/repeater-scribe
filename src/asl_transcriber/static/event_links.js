(() => {
  const path = location.pathname;
  const makeLink = (label, href) => {const node = document.createElement('a');node.textContent = label;node.href = href;return node;};
  const box = document.createElement('aside');box.className = 'event-related';box.setAttribute('aria-label', 'Related events');
  if (path === '/') {
    const main = document.querySelector('main');box.id = 'dashboard-events';main.before(box);
    async function refresh() {
      try {
        const response = await fetch('/api/v1/sessions?status=active&limit=25');
        if (!response.ok) throw new Error('Active events unavailable');
        const result = await response.json();box.replaceChildren(makeLink('Start Event', '/events?create=live'));
        result.items.forEach(item => {
          const row = document.createElement('p');row.append(makeLink(`ACTIVE · ${item.name} · ${item.source_label}`, `/events/${item.id}`));
          const control = document.createElement('button');control.type = 'button';control.className = 'quiet-button';control.textContent = 'End Event';
          control.addEventListener('click', async () => {
            control.disabled = true;
            try {const ended = await fetch(`/ui/sessions/${item.id}/end`, {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]')?.content || ''},body:'{}'});if (!ended.ok) throw new Error((await ended.json()).detail || 'Could not end event');await refresh();}
            catch (error) {const note = document.createElement('span');note.textContent = error.message;row.append(note);control.disabled = false;}
          });
          if (['operator','admin'].includes(document.body.dataset.role)) row.append(control);
          box.append(row);
        });
        if (!result.items.length) box.append(document.createTextNode(' · No active event'));
        if (result.has_more) box.append(makeLink('View all active events', '/events?status=active'));
      } catch (error) {box.textContent = error.message;box.append(makeLink('Open Events', '/events'));}
    }
    refresh();setInterval(() => {if (!document.hidden) refresh();}, 5000);
  } else if (path.startsWith('/archive/recordings/')) {
    document.querySelector('main').prepend(box);
    box.append(makeLink('Events containing this recording', `/events?recording_id=${encodeURIComponent(path.split('/')[3])}`));
    const offset = Number(new URLSearchParams(location.search).get('offset'));
    if (Number.isFinite(offset) && offset >= 0) {
      const observer = new MutationObserver(() => {
        const audio = document.querySelector('audio');if (!audio) return;
        const seek = () => {audio.currentTime = Math.min(offset, Number.isFinite(audio.duration) ? audio.duration : offset);};
        if (audio.readyState) seek();else audio.addEventListener('loadedmetadata', seek, {once:true});observer.disconnect();
      });observer.observe(document.querySelector('main'), {childList:true, subtree:true});
    }
  } else if (path.startsWith('/callsigns/')) {
    document.querySelector('main').prepend(box);
    box.append(makeLink('Events with a confirmed check-in for this station', `/events?callsign=${encodeURIComponent(decodeURIComponent(path.split('/')[2]))}`));
  }
})();
