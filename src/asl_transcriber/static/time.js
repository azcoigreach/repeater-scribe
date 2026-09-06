/* API instants are UTC; calendar fields and presentation use the browser zone. */
window.UITime = (() => {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const formatter = new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium', timeStyle: 'medium', timeZone: zone,
  });
  const hasOffset = value => /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  function instant(value) {
    if (value == null || value === '') return null;
    // Older API responses may contain SQLite's naive UTC representation.
    const date = new Date(typeof value === 'string' && !hasOffset(value) ? `${value}Z` : value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  function format(value, fallback = 'Time unavailable') {
    const date = instant(value);
    return date ? formatter.format(date) : fallback;
  }
  const pad = (value, length = 2) => String(value).padStart(length, '0');
  function localInput(value) {
    const date = instant(value);
    if (!date) return '';
    return `${pad(date.getFullYear(), 4)}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
  }
  function toUTC(value, endOfDay = false) {
    if (!value) return null;
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
    const input = dateOnly ? `${value}T${endOfDay ? '23:59:59.999' : '00:00:00.000'}` : value;
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?$/.test(input)) throw new Error('Enter a valid local date and time.');
    const date = new Date(input);
    const normalized = input.length === 16 ? `${input}:00.000` : input.length === 19 ? `${input}.000` : input.padEnd(23, '0');
    // Reject invalid dates and spring-forward times rather than silently shifting them.
    if (localInput(date) !== normalized) throw new Error(`This date and time does not exist in ${zone}. Choose another time.`);
    // The API/database support microseconds; include the entire last millisecond
    // when a date-only filter asks for the whole local day.
    return dateOnly && endOfDay ? date.toISOString().replace('.999Z', '.999999Z') : date.toISOString();
  }
  function restore(value, endOfDay = false) {
    if (!value) return '';
    if (hasOffset(value)) return localInput(value);
    // Preserve old bookmarks containing browser-local dates or wall times.
    try { return localInput(toUTC(value, endOfDay)); } catch (_) { return ''; }
  }
  const originals = new WeakMap();
  function setInput(input, value, endOfDay = false) {
    input.value = typeof value === 'string' ? restore(value, endOfDay) : localInput(value);
    const date = instant(value);
    // Keep the original instant when an unchanged calendar is submitted. This
    // preserves sub-millisecond precision and the second occurrence of a time
    // during a daylight-saving fall-back (which a wall time cannot distinguish).
    originals.set(input, {
      local: input.value,
      utc: input.value && date
        ? (typeof value === 'string' ? (hasOffset(value) ? value : toUTC(value, endOfDay)) : date.toISOString()) : null,
    });
  }
  function inputUTC(input, endOfDay = false) {
    const original = originals.get(input);
    if (input.value && original?.utc && original.local === input.value) return original.utc;
    return toUTC(input.value, endOfDay);
  }
  function label() {
    document.querySelectorAll('.timezone-help').forEach(node => {
      const description = `Times shown and entered in ${zone} (browser local).`;
      node.textContent = node.hasAttribute('data-compact') ? zone : description;
      node.title = description;
    });
  }
  document.addEventListener('DOMContentLoaded', label);
  return { zone, instant, format, localInput, toUTC, restore, setInput, inputUTC };
})();
