// Small helpers shared by every view.
// Rule: user-provided text (usernames, notes, tags) is only ever inserted with textContent
// (through h()), never as HTML, so a note like "<img onerror=...>" is displayed, not run.

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'on') for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...kids) {
  clear(el);
  append(el, kids);
  return el;
}

// ---------------------------------------------------------------- number formatting
const MINUS = '−';

export function num(n, decimals = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  return Number(n).toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function money(n, { sign = false, decimals = 2 } = {}) {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  const abs = num(Math.abs(n), decimals);
  if (n < 0 && Number(abs.replace(/[,.]/g, '')) !== 0) return `${MINUS}₹${abs}`;
  return `${sign && n > 0 ? '+' : ''}₹${abs}`;
}

export function pct(n, { sign = false, decimals = 2 } = {}) {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  const abs = num(Math.abs(n), decimals);
  if (n < 0 && Number(abs.replace(/[,.]/g, '')) !== 0) return `${MINUS}${abs}%`;
  return `${sign && n > 0 ? '+' : ''}${abs}%`;
}

export function rMult(n, decimals = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  const abs = num(Math.abs(n), decimals);
  if (n < 0 && Number(abs.replace(/[,.]/g, '')) !== 0) return `${MINUS}${abs}R`;
  return `${n > 0 ? '+' : ''}${abs}R`;
}

export function price(_market, p) {
  if (p === null || p === undefined) return '–';
  return num(p, 2);
}

export function qtyText(_market, q) {
  if (q === null || q === undefined) return '–';
  return num(q, 0);
}

// "2 lots · 130" for futures and options, "50 shares" for stocks. `x` is a position/order/trade
// view or an instrument (anything with kind, lot_size and qty).
export function sizeText(x, qty = x.qty) {
  if (qty === null || qty === undefined) return '–';
  if (x.kind && x.kind !== 'equity' && x.lot_size > 1) {
    const lots = Math.round(qty / x.lot_size);
    return `${num(lots, 0)} lot${lots === 1 ? '' : 's'} · ${num(qty, 0)}`;
  }
  return `${num(qty, 0)} ${qty === 1 ? 'share' : 'shares'}`;
}

// 'up' | 'down' | '' : paired with a sign or arrow wherever it is shown (never colour alone)
export function tone(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '';
  if (Math.abs(n) < 0.005) return '';
  return n > 0 ? 'up' : 'down';
}

export function arrow(n) {
  const t = tone(n);
  return t === 'up' ? '▲' : t === 'down' ? '▼' : '';
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return '–';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const hr = Math.floor(m / 60);
  if (hr < 48) return `${hr}h ${m % 60}m`;
  return `${Math.floor(hr / 24)}d ${hr % 24}h`;
}

export function dateTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
}

// ---------------------------------------------------------------- toast & dialogs
let toastTimer = null;
export function toast(message, kind = 'info') {
  let host = document.getElementById('toast');
  if (!host) return;
  host.className = `toast show ${kind}`;
  host.textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { host.className = 'toast'; }, kind === 'error' ? 6000 : 3200);
}

export function debounce(fn, ms) {
  let t;
  const wrapped = (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  wrapped.cancel = () => clearTimeout(t);
  return wrapped;
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function storage(key, value) {
  // localStorage can throw (private windows, blocked storage): the app works without it.
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (_) { /* ignore */ }
  return null;
}
