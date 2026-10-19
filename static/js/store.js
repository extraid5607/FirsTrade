// Shared state: who is logged in, the market list, live prices and the account.
// Views subscribe to events instead of polling on their own.
import { api } from './api.js';

const listeners = new Map();
export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}
export function emit(event, payload) {
  (listeners.get(event) || []).forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } });
}

export const store = {
  me: null,          // { app_name, demo, user, limits }
  markets: [],       // watch list: indices and stocks (from /api/markets)
  byId: {},
  extra: {},         // futures/options that were opened (loaded on demand)
  market: null,      // { open, label, next_open, ... } exchange clock
  pendingSide: null, // 'long' | 'short' picked on the option chain, used by the next ticket render
  prices: {},        // id -> { p, c, f }
  account: null,
  symbol: null,
  tf: '15m',
  handled: new Set(), // "trade:12" / "order:3" the user closed or cancelled themselves (no alert needed)
  quietUntil: 0,      // no auto-close alerts before this time (used right after an account reset)
};

export function market(id) {
  return store.byId[id] || store.extra[id]
    || { id, name: id, label: id, kind: 'equity', lot_size: 1, tick: 0.05, tradable: true, products: ['MIS'] };
}

// Futures and options are not in the watch list: fetch their details when first needed.
export async function ensureInstrument(id) {
  if (store.byId[id] || store.extra[id]) return market(id);
  const d = await api(`/api/instrument/${encodeURIComponent(id)}`);
  store.extra[id] = d;
  if (d.price != null) store.prices[id] = { p: d.price, c: d.change, f: d.live ? 1 : 0 };
  return d;
}
export function markHandled(kind, id) { store.handled.add(`${kind}:${id}`); }
export function stayQuiet(ms = 4000) { store.quietUntil = Date.now() + ms; }

export async function loadMe() {
  store.me = await api('/api/me');
  store.market = store.me.market;
  return store.me;
}

export async function loadMarkets() {
  const { markets } = await api('/api/markets');
  store.markets = markets;
  store.byId = Object.fromEntries(markets.map((m) => [m.id, m]));
  for (const m of markets) {
    if (m.price != null) store.prices[m.id] = { p: m.price, c: m.change, f: m.live ? 1 : 0 };
  }
  if (!store.symbol || !(store.byId[store.symbol] || store.extra[store.symbol])) store.symbol = markets.find((m) => m.id === 'NIFTY') ? 'NIFTY' : markets[0].id;
}

export function setAccount(acct) {
  store.account = acct;
  emit('account', acct);
}

export async function refreshAccount() {
  if (!store.me || !store.me.user) return null;
  const acct = await api('/api/account');
  setAccount(acct);
  return acct;
}

export async function setSymbol(id) {
  if (id === store.symbol) return;
  try { await ensureInstrument(id); } catch (_) { return; }
  store.symbol = id;
  emit('symbol', id);
}

export function setTimeframe(tf) {
  store.tf = tf;
  emit('timeframe', tf);
}

// ---------------------------------------------------------------- live prices
let ws = null;
let wsTimer = null;
let pollTimer = null;
let wsFailures = 0;

function applyPrices(q, d, open) {
  store.prices = { ...store.prices, ...q, ...(d || {}) };
  if (open !== undefined && store.market && store.market.open !== open) {
    store.market = { ...store.market, open };
    emit('market', store.market);
  }
  emit('prices', store.prices);
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try { const r = await api('/api/prices'); applyPrices(r.q, r.d); } catch (_) { /* retry next tick */ }
  }, 2000);
}

export function connectPrices() {
  if (ws) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  try { ws = new WebSocket(`${proto}://${location.host}/ws/prices`); } catch (_) { startPolling(); return; }
  ws.onopen = () => { wsFailures = 0; if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };
  ws.onmessage = (ev) => { try { const m = JSON.parse(ev.data); applyPrices(m.q, m.d, m.open); } catch (_) { /* ignore bad frame */ } };
  ws.onclose = () => {
    ws = null;
    wsFailures += 1;
    if (wsFailures >= 3) startPolling();   // WebSockets blocked: fall back to polling
    clearTimeout(wsTimer);
    wsTimer = setTimeout(connectPrices, Math.min(15000, 1000 * wsFailures));
  };
  ws.onerror = () => { try { ws.close(); } catch (_) { /* ignore */ } };
}

// ---------------------------------------------------------------- account polling
let acctTimer = null;
export function startAccountPolling() {
  stopAccountPolling();
  acctTimer = setInterval(() => {
    if (document.hidden) return;
    refreshAccount().catch(() => {});
  }, 2000);
}
export function stopAccountPolling() { clearInterval(acctTimer); acctTimer = null; }

// ---------------------------------------------------------------- exchange clock
let clockTimer = null;
export function startMarketPolling() {
  clearInterval(clockTimer);
  clockTimer = setInterval(async () => {
    try { store.market = await api('/api/market'); emit('market', store.market); } catch (_) { /* retry */ }
  }, 30000);
}
