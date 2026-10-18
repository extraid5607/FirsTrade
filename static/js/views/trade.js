// The Trade screen: instrument list, live chart, order ticket and the positions panel.
import { api } from '../api.js';
import { h, mount, price, pct, tone, arrow, sizeText, cssVar, storage, toast, debounce } from '../util.js';
import { store, on, setSymbol, setTimeframe, market } from '../store.js';
import { createPriceChart } from '../chart.js';
import { createTicket } from '../ticket.js';

const TFS = [['1m', '1m'], ['5m', '5m'], ['15m', '15m'], ['1h', '1h'], ['1d', '1D']];
const TF_SEC = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '1d': 86400 };
const GROUPS = [['index', 'Indices'], ['equity', 'Stocks']];

function title(m) { return m.label || m.name; }

export function renderTrade(root) {
  const unsub = [];
  const cleanups = [];

  const savedSym = storage('zp_symbol');
  if (savedSym && (store.byId[savedSym] || store.extra[savedSym])) store.symbol = savedSym;
  const savedTf = storage('zp_tf');
  if (savedTf && TF_SEC[savedTf]) store.tf = savedTf;
  if (!TF_SEC[store.tf]) store.tf = '5m';

  const marketsEl = h('aside', { class: 'panel markets', 'aria-label': 'Instruments' });
  const chartPanel = h('section', { class: 'panel chart-panel', 'aria-label': 'Price chart' });
  const ticketRoot = h('aside', { class: 'panel ticket', 'aria-label': 'Order ticket' });
  mount(root, h('div', { class: 'page' },
    h('div', { class: 'workspace' }, marketsEl, h('div', { class: 'chart-col' }, chartPanel), ticketRoot)));

  // ---------------------------------------------------------------- instrument list + search
  let rows = {};
  let listed = [];            // instruments currently shown in the list
  let searchSeq = 0;
  const search = h('input', { type: 'search', class: 'msearch', placeholder: 'Search symbol…', 'aria-label': 'Search instruments', autocomplete: 'off', spellcheck: 'false', maxlength: '30' });
  const listEl = h('div', { class: 'mlist' });
  mount(marketsEl, search, listEl);

  function makeRow(m, sub) {
    const r = { price: h('div', { class: 'mrow-price' }), chg: h('div', { class: 'mrow-chg' }) };
    r.btn = h('button', { type: 'button', class: 'mrow', on: { click: () => { setSymbol(m.id); storage('zp_symbol', m.id); } } },
      h('div', { class: 'mrow-name' }, m.label || m.name),
      r.price,
      h('div', { class: 'mrow-sub' }, sub || ''),
      r.chg);
    rows[m.id] = r;
    listed.push(m);
    return r.btn;
  }

  function drawList(items) {
    rows = {}; listed = [];
    if (items) {
      mount(listEl, items.length
        ? h('div', { class: 'mgroup' }, items.map((m) => makeRow(m, '')))
        : h('p', { class: 'fine', style: { padding: '8px' } }, 'Nothing matches. Try a share name such as TCS, or NIFTY.'));
    } else {
      mount(listEl, GROUPS.map(([kind, label]) => {
        const ms = store.markets.filter((m) => m.kind === kind);
        if (!ms.length) return null;
        return h('div', { class: 'mgroup' }, h('div', { class: 'mgroup-title' }, label), ms.map((m) => makeRow(m, '')));
      }));
    }
    paintMarkets();
  }

  const runSearch = debounce(async () => {
    const q = search.value.trim();
    if (!q) { drawList(null); return; }
    const mine = ++searchSeq;
    try {
      const r = await api('/api/markets', { params: { q } });
      if (mine !== searchSeq) return;
      for (const m of r.markets) {
        if (m.price != null && !store.prices[m.id]) store.prices[m.id] = { p: m.price, c: m.change, f: m.live ? 1 : 0 };
        if (!store.byId[m.id]) store.extra[m.id] = m;
      }
      drawList(r.markets);
    } catch (e) { toast(e.message, 'error'); }
  }, 250);
  search.addEventListener('input', runSearch);

  function paintMarkets() {
    for (const m of listed) {
      const r = rows[m.id];
      if (!r) continue;
      const q = store.prices[m.id];
      r.price.textContent = q ? price(m, q.p) : '–';
      if (q && !q.f) { r.chg.textContent = 'Closed'; r.chg.className = 'mrow-chg'; }
      else if (q && q.c != null) { r.chg.textContent = `${arrow(q.c)} ${pct(q.c, { sign: true })}`.trim(); r.chg.className = `mrow-chg ${tone(q.c)}`; }
      else { r.chg.textContent = ''; r.chg.className = 'mrow-chg'; }
      r.btn.setAttribute('aria-current', String(m.id === store.symbol));
    }
  }

  // ---------------------------------------------------------------- chart
  const titleEl = h('h2');
  const priceEl = h('span', { class: 'chart-price' });
  const chgEl = h('span', { class: 'chart-chg' });
  const tfSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Timeframe' });
  const fnoEl = h('div', { class: 'fno-bar', style: { display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center', padding: '0 12px 8px' } });
  const box = h('div', { class: 'chart-box' });
  const noteEl = h('div', { class: 'chart-note' }, 'Loading chart…');
  box.append(noteEl);

  let ticket = null;
  const buyBtnPrice = h('span', { class: 'trade-action-price' }, '–');
  const sellBtnPrice = h('span', { class: 'trade-action-price' }, '–');
  const buyBtn = h('button', {
    type: 'button', class: 'trade-action-btn buy', 'aria-label': 'Buy order',
    on: {
      click: () => {
        if (ticket) {
          ticket.setSide('long');
          ticketRoot.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    }
  },
    h('span', { class: 'trade-action-label' }, '▲ BUY'),
    buyBtnPrice
  );
  const sellBtn = h('button', {
    type: 'button', class: 'trade-action-btn sell', 'aria-label': 'Sell order',
    on: {
      click: () => {
        if (ticket) {
          ticket.setSide('short');
          ticketRoot.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    }
  },
    h('span', { class: 'trade-action-label' }, '▼ SELL'),
    sellBtnPrice
  );
  const actionBar = h('div', { class: 'trade-action-bar' }, buyBtn, sellBtn);

  mount(chartPanel,
    h('div', { class: 'chart-head' }, h('div', { class: 'chart-title' }, titleEl, priceEl, chgEl), tfSeg),
    fnoEl,
    box,
    actionBar);

  let chart = null;
  if (window.LightweightCharts) {
    chart = createPriceChart(box);
  } else {
    mount(noteEl, 'The chart library did not load. Reload the page to try again.');
  }

  function drawTfs() {
    mount(tfSeg, TFS.map(([tf, label]) => h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(tf === store.tf),
      on: { click: () => { if (tf === store.tf) return; setTimeframe(tf); storage('zp_tf', tf); drawTfs(); loadCandles(); } },
    }, label)));
  }

  function paintHead() {
    const m = market(store.symbol);
    const q = store.prices[store.symbol];
    titleEl.textContent = title(m);
    const pStr = q ? price(m, q.p) : '–';
    priceEl.textContent = pStr;
    buyBtnPrice.textContent = pStr;
    sellBtnPrice.textContent = pStr;
    if (m && m.tradable === false) {
      buyBtn.disabled = true;
      sellBtn.disabled = true;
    } else {
      buyBtn.disabled = false;
      sellBtn.disabled = false;
    }
    if (q && !q.f) { chgEl.textContent = 'Market closed'; chgEl.className = 'chart-chg muted'; }
    else if (q && q.c != null) { chgEl.textContent = `${arrow(q.c)} ${pct(q.c, { sign: true })} today`.replace('  ', ' ').trim(); chgEl.className = `chart-chg ${tone(q.c)}`; }
    else { chgEl.textContent = ''; chgEl.className = 'chart-chg'; }
  }

  // ---------------------------------------------------------------- F&O strip (futures + option chain link)
  const expCache = {};
  let fnoSeq = 0;
  async function drawFno() {
    const m = market(store.symbol);
    const und = m.kind === 'future' || m.kind === 'option' ? m.underlying : (m.fno ? m.id : null);
    if (!und) { mount(fnoEl); fnoEl.hidden = true; return; }
    fnoEl.hidden = false;
    const mine = ++fnoSeq;
    let e = expCache[und];
    if (!e) {
      try { e = expCache[und] = await api('/api/expiries', { params: { underlying: und } }); } catch (_) { mount(fnoEl); return; }
    }
    if (mine !== fnoSeq) return;
    const kids = [];
    if (und !== m.id) {
      kids.push(h('button', { type: 'button', class: 'btn sm ghost', on: { click: () => { setSymbol(und); storage('zp_symbol', und); } } }, `← ${und}`));
    }
    kids.push(h('span', { class: 'muted' }, 'Futures:'));
    for (const f of e.futures) {
      kids.push(h('button', { type: 'button', class: 'chip', 'aria-pressed': String(f.id === m.id),
        on: { click: () => { setSymbol(f.id); storage('zp_symbol', f.id); } } }, f.label.replace(`${und} `, '')));
    }
    kids.push(h('a', { class: 'btn sm', href: `#/chain?u=${encodeURIComponent(und)}` }, 'Option chain'));
    mount(fnoEl, kids);
  }

  let loadId = 0;
  async function loadCandles() {
    if (!chart) return;
    const id = ++loadId;
    const m = market(store.symbol);
    const tf = store.tf;
    mount(noteEl, 'Loading chart…');
    noteEl.hidden = false;
    try {
      const r = await api('/api/candles', { params: { symbol: m.id, tf, limit: 300 } });
      if (id !== loadId) return;
      chart.setCandles(r.candles, m, TF_SEC[tf]);
      if (!r.candles.length) { mount(noteEl, 'No candles for this instrument yet.'); noteEl.hidden = false; return; }
      noteEl.hidden = true;
      syncLines();
    } catch (e) {
      if (id !== loadId) return;
      mount(noteEl, h('p', null, e.message), h('button', { type: 'button', class: 'btn sm', on: { click: loadCandles } }, 'Try again'));
      noteEl.hidden = false;
    }
  }

  // Lines for open positions, pending orders and the order you are drafting in the ticket.
  let draft = null;
  function syncLines() {
    if (!chart) return;
    const sym = store.symbol;
    const spec = [];
    const a = store.account;
    const down = cssVar('--chart-down'), up = cssVar('--chart-up'), accent = cssVar('--accent'), warn = cssVar('--warn');
    if (a) {
      for (const p of a.positions) {
        if (p.symbol !== sym) continue;
        spec.push({ key: `e${p.id}`, price: p.entry, color: accent, width: 2, title: `${p.side === 'long' ? 'Buy' : 'Sell'} ${sizeText(p)}` });
        if (p.sl != null) spec.push({ key: `s${p.id}`, price: p.sl, color: down, style: 'dashed', title: 'Stop' });
        if (p.tp != null) spec.push({ key: `t${p.id}`, price: p.tp, color: up, style: 'dashed', title: 'Target' });
        if (p.liq != null) spec.push({ key: `l${p.id}`, price: p.liq, color: warn, style: 'dotted', title: 'Auto-exit' });
      }
      for (const o of a.orders) {
        if (o.symbol !== sym) continue;
        spec.push({ key: `o${o.id}`, price: o.limit_price, color: accent, style: 'dotted', title: o.side === 'long' ? 'Buy limit' : 'Sell limit' });
      }
    }
    if (draft && draft.symbol === sym) {
      if (draft.entry) spec.push({ key: 'd-entry', price: draft.entry, color: accent, style: 'dotted', title: 'Limit (new)' });
      if (draft.sl) spec.push({ key: 'd-sl', price: draft.sl, color: down, style: 'dotted', title: 'Stop (new)' });
      if (draft.tp) spec.push({ key: 'd-tp', price: draft.tp, color: up, style: 'dotted', title: 'Target (new)' });
      if (draft.liq) spec.push({ key: 'd-liq', price: draft.liq, color: warn, style: 'dotted', title: 'Auto-exit (new)' });
    }
    chart.setLines(spec);
  }

  // ---------------------------------------------------------------- wiring
  unsub.push(on('prices', () => {
    paintMarkets();
    paintHead();
    const q = store.prices[store.symbol];
    if (chart && q && q.f) chart.tick(q.p);
  }));
  unsub.push(on('symbol', () => { draft = null; paintMarkets(); paintHead(); drawFno(); loadCandles(); syncLines(); }));
  unsub.push(on('account', syncLines));
  unsub.push(on('draft', (d) => { draft = d; syncLines(); }));
  unsub.push(on('theme', () => { if (chart) { chart.applyTheme(); syncLines(); } }));

  drawTfs();
  drawList(null);
  paintHead();
  drawFno();
  ticket = createTicket(ticketRoot);
  loadCandles();
  syncLines();

  return function destroy() {
    unsub.forEach((f) => f());
    cleanups.forEach((f) => f());
    runSearch.cancel && runSearch.cancel();
    ticket.destroy();
    if (chart) chart.destroy();
  };
}
