import { api } from '../api.js';
import { h, mount, price, pct, tone, arrow, debounce, storage } from '../util.js';
import { store, on, setSymbol } from '../store.js';

const GROUPS = [
  ['index', 'Indices'],
  ['equity', 'Stocks'],
];

export function renderWatchlist(root) {
  const unsub = [];
  let rows = {};
  let listed = [];
  let searchSeq = 0;

  const search = h('input', {
    type: 'search',
    class: 'msearch-full',
    placeholder: 'Search symbol (e.g. NIFTY, BANKNIFTY, RELIANCE, TCS)...',
    'aria-label': 'Search instruments',
    autocomplete: 'off',
    spellcheck: 'false',
    maxlength: '30',
  });

  const listEl = h('div', { class: 'watchlist-list' });

  function makeRow(m) {
    const r = {
      price: h('div', { class: 'wl-price' }),
      chg: h('div', { class: 'wl-chg' }),
    };
    r.btn = h('button', {
      type: 'button',
      class: 'wl-row',
      on: {
        click: async () => {
          await setSymbol(m.id);
          storage('zp_symbol', m.id);
          location.hash = '#/trade';
        },
      },
    },
      h('div', { class: 'wl-info' },
        h('div', { class: 'wl-name' }, m.label || m.name),
        h('div', { class: 'wl-sub' },
          h('span', { class: 'badge sm' }, m.kind === 'index' ? 'INDEX' : 'NSE'),
          m.lot_size > 1 ? h('span', { class: 'badge sm' }, `Lot ${m.lot_size}`) : null)),
      h('div', { class: 'wl-numbers' },
        r.price,
        r.chg));
    rows[m.id] = r;
    listed.push(m);
    return r.btn;
  }

  function drawList(items) {
    rows = {};
    listed = [];
    if (items) {
      mount(listEl, items.length
        ? h('div', { class: 'wl-group' }, items.map((m) => makeRow(m)))
        : h('p', { class: 'empty' }, 'No instruments found. Try searching for NIFTY, BANKNIFTY, or TCS.'));
    } else {
      mount(listEl, GROUPS.map(([kind, label]) => {
        const ms = store.markets.filter((m) => m.kind === kind);
        if (!ms.length) return null;
        return h('div', { class: 'wl-group' },
          h('div', { class: 'wl-group-title' }, label),
          h('div', { class: 'wl-items' }, ms.map((m) => makeRow(m))));
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
        if (m.price != null && !store.prices[m.id]) {
          store.prices[m.id] = { p: m.price, c: m.change, f: m.live ? 1 : 0 };
        }
        if (!store.byId[m.id]) store.extra[m.id] = m;
      }
      drawList(r.markets);
    } catch (e) {
      console.error(e);
    }
  }, 250);

  search.addEventListener('input', runSearch);

  function paintMarkets() {
    for (const m of listed) {
      const r = rows[m.id];
      if (!r) continue;
      const q = store.prices[m.id];
      r.price.textContent = q ? price(m, q.p) : '–';
      if (q && !q.f) {
        r.chg.textContent = 'Market closed';
        r.chg.className = 'wl-chg muted';
      } else if (q && q.c != null) {
        r.chg.textContent = `${arrow(q.c)} ${pct(q.c, { sign: true })}`.trim();
        r.chg.className = `wl-chg ${tone(q.c)}`;
      } else {
        r.chg.textContent = '';
        r.chg.className = 'wl-chg';
      }
    }
  }

  unsub.push(on('prices', paintMarkets));

  mount(root, h('div', { class: 'page page-narrow' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Watchlist'),
      h('p', { class: 'page-sub' }, 'Tap any stock or index to view its live chart and trade.')),
    h('div', { class: 'panel wl-panel' },
      h('div', { class: 'wl-search-bar' }, search),
      listEl)));

  drawList(null);

  return function destroy() {
    unsub.forEach((f) => f());
  };
}
