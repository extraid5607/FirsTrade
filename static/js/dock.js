// The tabbed panel under the chart: open positions, pending orders and recent history.
// Tables are updated cell by cell (never rebuilt) so buttons stay put while prices tick.
import { api } from './api.js';
import { h, mount, money, pct, rMult, price, sizeText, tone, arrow, dateTime, toast } from './util.js';
import { store, on, emit, market, setAccount, markHandled } from './store.js';
import { openStopsDialog, openJournalDialog, confirmDialog, REASONS } from './dialogs.js';

function sideBadge(side, extra = '') {
  return h('span', { class: `badge ${side === 'long' ? 'up' : 'down'}` }, side === 'long' ? '▲ Buy' : '▼ Sell', extra);
}

function labelOf(x) { return x.label || market(x.symbol).name; }

// Generic table whose rows are created once and then only have their cells refreshed.
// column: { label, cls, fill(td, item), once }  (once: fill only when the row is created)
function keyedTable(columns, getKey) {
  const tbody = h('tbody');
  const table = h('table', { class: 'tbl tbl-cards' },
    h('thead', null, h('tr', null, columns.map((c) => h('th', { class: c.cls || '' }, c.label || '')))),
    tbody);
  const rows = new Map();

  function update(items) {
    const keys = new Set(items.map(getKey));
    for (const [k, row] of rows) {
      if (!keys.has(k)) { row.tr.remove(); rows.delete(k); }
    }
    let prev = null;
    for (const item of items) {
      const k = getKey(item);
      let row = rows.get(k);
      if (!row) {
        const tds = columns.map((c) => h('td', { class: c.cls || '', 'data-label': c.label || null }));
        row = { tr: h('tr', null, tds), tds };
        columns.forEach((c, i) => c.fill(tds[i], item));
        rows.set(k, row);
      } else {
        columns.forEach((c, i) => { if (!c.once) c.fill(row.tds[i], item); });
      }
      const ref = prev ? prev.nextSibling : tbody.firstChild;
      if (row.tr !== ref) tbody.insertBefore(row.tr, ref);
      prev = row.tr;
    }
  }
  return { el: table, update };
}

export function createDock(root) {
  const unsub = [];
  let tab = 'positions';
  let history = null;          // { total, trades } once loaded
  let historyStale = true;
  let historyBusy = false;

  const tabs = h('div', { class: 'dock-tabs', role: 'tablist' });
  const body = h('div', { class: 'dock-body', role: 'tabpanel' });
  mount(root, tabs, body);

  // ---------------------------------------------------------------- positions
  const posTable = keyedTable([
    { label: 'Instrument', cls: 'sym', once: true, fill: (td, p) => { td.append(labelOf(p), h('span', { class: 'sub' }, sideBadge(p.side), ' ', h('span', { class: 'badge' }, p.product))); } },
    { label: 'Size', cls: 'r', once: true, fill: (td, p) => { td.append(sizeText(p), h('span', { class: 'sub' }, money(p.value, { decimals: 0 }))); } },
    { label: 'Entry', cls: 'r', once: true, fill: (td, p) => { td.textContent = price(market(p.symbol), p.entry); } },
    { label: 'Price', cls: 'r', fill: (td, p) => { td.textContent = price(market(p.symbol), p.price); } },
    { label: 'P&L', cls: 'r', fill: (td, p) => {
      td.className = `r ${tone(p.upnl)}`;
      mount(td, `${arrow(p.upnl)} ${money(p.upnl, { sign: true })}`.trim(), h('span', { class: 'sub' }, pct(p.upnl_pct, { sign: true })));
    } },
    { label: 'R', cls: 'r', fill: (td, p) => { td.className = `r ${tone(p.r_now)}`; td.textContent = p.r_now != null ? rMult(p.r_now) : '–'; } },
    { label: 'Stop', cls: 'r', fill: (td, p) => { td.textContent = p.sl != null ? price(market(p.symbol), p.sl) : '–'; } },
    { label: 'Target', cls: 'r', fill: (td, p) => { td.textContent = p.tp != null ? price(market(p.symbol), p.tp) : '–'; } },
    { label: 'Auto-exit', cls: 'r', fill: (td, p) => {
      if (p.liq == null) { mount(td, '–', h('span', { class: 'sub' }, 'none')); return; }
      const near = p.liq_distance_pct != null && p.liq_distance_pct < 5;
      mount(td, price(market(p.symbol), p.liq), h('span', { class: `sub ${near ? 'down' : ''}` }, p.liq_distance_pct != null ? `${pct(p.liq_distance_pct, { decimals: 1 })} away` : ''));
    } },
    { label: '', cls: 'full', once: true, fill: (td, p) => {
      const closeBtn = h('button', { type: 'button', class: 'btn sm', on: { click: () => closePosition(p.id, closeBtn) } }, 'Close');
      td.append(h('div', { class: 'acts' },
        h('button', { type: 'button', class: 'btn sm ghost', on: { click: () => { const cur = (store.account?.positions || []).find((x) => x.id === p.id); if (cur) openStopsDialog(cur); } } }, 'Edit stops'),
        closeBtn));
    } },
  ], (p) => p.id);

  async function closePosition(id, btn) {
    btn.disabled = true;
    markHandled('trade', id);
    try {
      const r = await api(`/api/trades/${id}/close`, { method: 'POST' });
      setAccount(r.account);
      const t = r.trade;
      toast(`Closed ${labelOf(t)}: ${money(t.pnl, { sign: true })}${t.r != null ? ` (${rMult(t.r)})` : ''}`, t.pnl >= 0 ? 'ok' : 'info');
      historyStale = true;
      if (tab === 'history') loadHistory();
    } catch (e) {
      store.handled.delete(`trade:${id}`);
      btn.disabled = false;
      toast(e.message, 'error');
    }
  }

  // ---------------------------------------------------------------- orders
  const ordTable = keyedTable([
    { label: 'Instrument', cls: 'sym', once: true, fill: (td, o) => { td.append(labelOf(o), h('span', { class: 'sub' }, sideBadge(o.side), ' ', h('span', { class: 'badge' }, o.product))); } },
    { label: 'Type', once: true, fill: (td, o) => { td.textContent = o.side === 'long' ? 'Buy limit' : 'Sell limit'; } },
    { label: 'Size', cls: 'r', once: true, fill: (td, o) => { td.textContent = sizeText(o); } },
    { label: 'Limit price', cls: 'r', once: true, fill: (td, o) => { td.textContent = price(market(o.symbol), o.limit_price); } },
    { label: 'Price now', cls: 'r', fill: (td, o) => { const q = store.prices[o.symbol]; td.textContent = q ? price(market(o.symbol), q.p) : '–'; } },
    { label: 'Stop', cls: 'r', once: true, fill: (td, o) => { td.textContent = o.sl != null ? price(market(o.symbol), o.sl) : '–'; } },
    { label: 'Target', cls: 'r', once: true, fill: (td, o) => { td.textContent = o.tp != null ? price(market(o.symbol), o.tp) : '–'; } },
    { label: 'Margin held', cls: 'r', once: true, fill: (td, o) => { td.textContent = money(o.reserved); } },
    { label: '', cls: 'full', once: true, fill: (td, o) => {
      const btn = h('button', { type: 'button', class: 'btn sm', on: { click: () => cancelOrder(o.id, btn) } }, 'Cancel');
      td.append(h('div', { class: 'acts' }, btn));
    } },
  ], (o) => o.id);

  async function cancelOrder(id, btn) {
    btn.disabled = true;
    markHandled('order', id);
    try {
      const r = await api(`/api/orders/${id}`, { method: 'DELETE' });
      setAccount(r.account);
      toast('Order cancelled. Its margin is free again.', 'info');
    } catch (e) {
      store.handled.delete(`order:${id}`);
      btn.disabled = false;
      toast(e.message, 'error');
    }
  }

  // ---------------------------------------------------------------- history
  async function loadHistory() {
    if (historyBusy) return;
    historyBusy = true;
    try {
      history = await api('/api/trades', { params: { limit: 15 } });
      historyStale = false;
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      historyBusy = false;
      if (tab === 'history') renderBody();
    }
  }

  function renderHistory() {
    if (!history) return h('p', { class: 'empty' }, 'Loading your trades…');
    if (!history.trades.length) {
      return h('div', { class: 'empty' }, h('strong', null, 'No closed trades yet'), 'Close a position and it shows up here, ready for a journal note.');
    }
    const t = h('table', { class: 'tbl tbl-cards' },
      h('thead', null, h('tr', null, ['Closed', 'Instrument', 'Entry', 'Exit', 'P&L', 'R', 'Result', ''].map((x, i) => h('th', { class: i >= 2 && i <= 5 ? 'r' : '' }, x)))),
      h('tbody', null, history.trades.map((x) => {
        const m = market(x.symbol);
        return h('tr', null,
          h('td', { 'data-label': 'Closed' }, dateTime(x.closed_at)),
          h('td', { class: 'sym', 'data-label': 'Instrument' }, labelOf(x), ' ', sideBadge(x.side)),
          h('td', { class: 'r', 'data-label': 'Entry' }, price(m, x.entry)),
          h('td', { class: 'r', 'data-label': 'Exit' }, price(m, x.exit)),
          h('td', { class: `r ${tone(x.pnl)}`, 'data-label': 'P&L' }, `${arrow(x.pnl)} ${money(x.pnl, { sign: true })}`.trim()),
          h('td', { class: `r ${tone(x.r)}`, 'data-label': 'R' }, x.r != null ? rMult(x.r) : '–'),
          h('td', { 'data-label': 'Result' }, h('span', { class: `badge ${x.reason === 'rms' ? 'down' : ''}` }, REASONS[x.reason] || x.reason)),
          h('td', { class: 'full' }, h('div', { class: 'acts' },
            h('button', { type: 'button', class: 'btn sm ghost', on: { click: () => openJournalDialog(x, (nt) => { Object.assign(x, nt); }) } }, x.note || x.lesson ? 'Edit journal' : 'Add journal'))));
      })));
    return h('div', null, t,
      h('p', { class: 'fine', style: { padding: '8px 12px' } }, history.total > history.trades.length ? `Showing the latest ${history.trades.length} of ${history.total}. ` : '', h('a', { href: '#/journal' }, 'Open the full journal')));
  }

  // ---------------------------------------------------------------- shell
  function drawTabs() {
    const a = store.account;
    const counts = { positions: a ? a.positions.length : 0, orders: a ? a.orders.length : 0 };
    mount(tabs, ['positions', 'orders', 'history'].map((k) => h('button', {
      type: 'button', class: 'dock-tab', role: 'tab', 'aria-selected': String(tab === k),
      on: { click: () => { tab = k; drawTabs(); renderBody(); if (k === 'history' && (historyStale || !history)) loadHistory(); } },
    }, k === 'positions' ? `Positions${counts.positions ? ` (${counts.positions})` : ''}`
      : k === 'orders' ? `Orders${counts.orders ? ` (${counts.orders})` : ''}` : 'History')));
  }

  const posEmpty = h('div', { class: 'empty' }, h('strong', null, 'No open positions'), 'Pick an instrument, set a stop-loss and place a trade from the order panel.');
  const ordEmpty = h('div', { class: 'empty' }, h('strong', null, 'No pending orders'), 'A limit order waits until the price reaches the level you choose, then opens a position. Unfilled orders lapse at the close.');

  function renderBody() {
    const a = store.account;
    if (!a) { mount(body); return; }
    if (tab === 'positions') {
      mount(body, a.positions.length ? posTable.el : posEmpty);
      posTable.update(a.positions);
    } else if (tab === 'orders') {
      mount(body, a.orders.length ? ordTable.el : ordEmpty);
      ordTable.update(a.orders);
    } else {
      mount(body, renderHistory());
    }
  }

  let lastCounts = '';
  unsub.push(on('account', (a) => {
    const counts = `${a.positions.length}/${a.orders.length}`;
    if (counts !== lastCounts) { lastCounts = counts; drawTabs(); }
    if (tab === 'positions') {
      const showing = body.firstChild === posTable.el;
      if (showing !== !!a.positions.length) renderBody(); else posTable.update(a.positions);
    } else if (tab === 'orders') {
      const showing = body.firstChild === ordTable.el;
      if (showing !== !!a.orders.length) renderBody(); else ordTable.update(a.orders);
    }
  }));
  unsub.push(on('prices', (prices) => {
    if (tab === 'positions' && store.account && store.account.positions.length) {
      let anyChanged = false;
      for (const p of store.account.positions) {
        const q = prices && prices[p.symbol];
        if (q && q.p != null && q.p !== p.price) {
          p.price = q.p;
          const diff = p.side === 'long' ? (p.price - p.entry) : (p.entry - p.price);
          p.upnl = diff * p.qty;
          p.upnl_pct = p.margin ? (p.upnl / p.margin) * 100 : 0;
          if (p.initial_risk) p.r_now = p.upnl / p.initial_risk;
          if (p.liq) p.liq_distance_pct = Math.abs(p.price - p.liq) / p.price * 100;
          anyChanged = true;
        }
      }
      if (anyChanged) {
        store.account.unrealized = store.account.positions.reduce((sum, x) => sum + (x.upnl || 0), 0);
        store.account.equity = (store.account.cash || 0) + store.account.unrealized;
        store.account.day_pnl = (store.account.realized || 0) + store.account.unrealized;
        posTable.update(store.account.positions);
        emit('account', store.account);
      }
    } else if (tab === 'orders' && store.account && store.account.orders.length) {
      ordTable.update(store.account.orders);
    }
  }));
  unsub.push(on('closed', () => { historyStale = true; if (tab === 'history') loadHistory(); }));

  drawTabs();
  renderBody();

  return { destroy() { unsub.forEach((f) => f()); mount(root); } };
}
