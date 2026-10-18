import { api } from '../api.js';
import { h, mount, price, pct, tone, arrow, debounce, storage, toast } from '../util.js';
import { store, on, setSymbol, market, ensureInstrument } from '../store.js';

const DEFAULT_WATCHLISTS = [
  {
    id: 'wl_1',
    name: 'Watchlist 1',
    symbols: ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'RELIANCE', 'HDFCBANK', 'ICICIBANK', 'TCS', 'INFY'],
  },
  {
    id: 'wl_2',
    name: 'Watchlist 2',
    symbols: ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL', 'SBIN', 'ITC', 'LT', 'BAJFINANCE', 'MARUTI', 'TITAN'],
  },
  {
    id: 'wl_3',
    name: 'Watchlist 3',
    symbols: ['TRENT', 'BEL', 'JIOFIN', 'ETERNAL', 'TATASTEEL', 'ADANIENT', 'COALINDIA', 'HINDALCO', 'M&M', 'SUNPHARMA'],
  },
  {
    id: 'wl_4',
    name: 'Watchlist 4',
    symbols: ['ONGC', 'NTPC', 'POWERGRID', 'WIPRO', 'CIPLA', 'JSWSTEEL', 'BPCL', 'EICHERMOT', 'ASIANPAINT', 'ULTRACEMCO'],
  },
  {
    id: 'wl_5',
    name: 'Watchlist 5',
    symbols: ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'BAJFINANCE', 'ADANIENT', 'SBIN', 'MARUTI', 'TITAN', 'KOTAKBANK'],
  },
];

function loadWatchlists() {
  try {
    const raw = storage('zp_watchlists');
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) return parsed;
    }
  } catch (_) {}
  return JSON.parse(JSON.stringify(DEFAULT_WATCHLISTS));
}

function saveWatchlists(wls) {
  storage('zp_watchlists', JSON.stringify(wls));
}

export function renderWatchlist(root) {
  const unsub = [];
  let rows = {};
  let listed = [];
  let searchSeq = 0;
  let isSearching = false;

  let watchlists = loadWatchlists();
  let savedActiveId = storage('zp_active_wl');
  let activeId = watchlists.some((w) => w.id === savedActiveId) ? savedActiveId : watchlists[0].id;

  const tabsWrap = h('div', { class: 'wl-tabs-bar', role: 'tablist', 'aria-label': 'Watchlists' });
  const controlsBar = h('div', { class: 'wl-controls-bar' });

  const search = h('input', {
    type: 'search',
    class: 'msearch-full',
    placeholder: 'Search symbol to add (e.g. NIFTY, RELIANCE, TCS)...',
    'aria-label': 'Search instruments',
    autocomplete: 'off',
    spellcheck: 'false',
    maxlength: '30',
  });

  const listEl = h('div', { class: 'watchlist-list' });

  function getActiveWl() {
    return watchlists.find((w) => w.id === activeId) || watchlists[0];
  }

  function renderTabs() {
    const active = getActiveWl();
    const tabEls = watchlists.map((w) => {
      const isCur = w.id === active.id;
      const btn = h('button', {
        type: 'button',
        class: 'wl-tab-btn',
        role: 'tab',
        'aria-selected': String(isCur),
        'aria-pressed': String(isCur),
        on: {
          click: () => {
            if (activeId === w.id && !search.value.trim()) return;
            activeId = w.id;
            storage('zp_active_wl', activeId);
            search.value = '';
            isSearching = false;
            renderTabs();
            renderControls();
            renderActiveWatchlist();
          },
        },
      },
        h('span', null, w.name),
        h('span', { class: 'wl-tab-count' }, String(w.symbols.length)));
      return btn;
    });

    const addBtn = h('button', {
      type: 'button',
      class: 'wl-add-tab-btn',
      'aria-label': 'Create new watchlist',
      on: { click: createNewWatchlist },
    }, '+ New');

    mount(tabsWrap, ...tabEls, addBtn);
  }

  function renderControls() {
    const active = getActiveWl();
    if (isSearching) {
      mount(controlsBar,
        h('span', { class: 'fine' }, `Search Results · Tap "+ Add" to add into ${active.name}`),
        h('div', { class: 'wl-control-actions' },
          h('button', {
            type: 'button',
            class: 'btn sm ghost',
            on: {
              click: () => {
                search.value = '';
                isSearching = false;
                renderControls();
                renderActiveWatchlist();
              },
            },
          }, 'Clear (✕)')));
      return;
    }

    const renameBtn = h('button', {
      type: 'button',
      class: 'btn sm ghost',
      title: 'Rename this watchlist',
      on: { click: renameCurrentWatchlist },
    }, '✏️ Rename');

    const deleteBtn = watchlists.length > 1
      ? h('button', {
        type: 'button',
        class: 'btn sm danger ghost',
        title: 'Delete this watchlist',
        on: { click: deleteCurrentWatchlist },
      }, '🗑️ Delete')
      : null;

    mount(controlsBar,
      h('div', null,
        h('strong', null, active.name),
        h('span', { class: 'fine', style: { marginLeft: '8px' } }, `${active.symbols.length} item${active.symbols.length === 1 ? '' : 's'}`)),
      h('div', { class: 'wl-control-actions' }, renameBtn, deleteBtn));
  }

  function createNewWatchlist() {
    const defaultName = `Watchlist ${watchlists.length + 1}`;
    const name = window.prompt('Enter name for the new watchlist:', defaultName);
    if (name === null) return;
    const finalName = name.trim() || defaultName;
    const newWl = {
      id: `wl_${Date.now()}`,
      name: finalName,
      symbols: [],
    };
    watchlists.push(newWl);
    saveWatchlists(watchlists);
    activeId = newWl.id;
    storage('zp_active_wl', activeId);
    search.value = '';
    isSearching = false;
    renderTabs();
    renderControls();
    renderActiveWatchlist();
    toast(`Created ${finalName}`, 'ok');
  }

  function renameCurrentWatchlist() {
    const active = getActiveWl();
    const name = window.prompt('Rename watchlist:', active.name);
    if (name === null) return;
    const finalName = name.trim() || active.name;
    active.name = finalName;
    saveWatchlists(watchlists);
    renderTabs();
    renderControls();
    toast(`Renamed to ${finalName}`, 'info');
  }

  function deleteCurrentWatchlist() {
    if (watchlists.length <= 1) {
      toast('At least one watchlist must be kept.', 'error');
      return;
    }
    const active = getActiveWl();
    const ok = window.confirm(`Delete "${active.name}" and its saved symbols?`);
    if (!ok) return;

    watchlists = watchlists.filter((w) => w.id !== active.id);
    saveWatchlists(watchlists);
    activeId = watchlists[0].id;
    storage('zp_active_wl', activeId);
    search.value = '';
    isSearching = false;
    renderTabs();
    renderControls();
    renderActiveWatchlist();
    toast(`Deleted ${active.name}`, 'info');
  }

  function makeWatchlistRow(m) {
    const active = getActiveWl();
    const r = {
      price: h('div', { class: 'wl-price' }),
      chg: h('div', { class: 'wl-chg' }),
    };

    const removeBtn = h('button', {
      type: 'button',
      class: 'wl-btn-remove',
      title: `Remove ${m.label || m.name} from ${active.name}`,
      'aria-label': `Remove ${m.label || m.name}`,
      on: {
        click: (e) => {
          e.stopPropagation();
          active.symbols = active.symbols.filter((s) => s !== m.id);
          saveWatchlists(watchlists);
          renderTabs();
          renderControls();
          renderActiveWatchlist();
          toast(`Removed ${m.label || m.name} from ${active.name}`, 'info');
        },
      },
    }, '✕');

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
      h('div', { class: 'wl-row-actions' },
        h('div', { class: 'wl-numbers' }, r.price, r.chg),
        removeBtn));

    rows[m.id] = r;
    listed.push(m);
    return r.btn;
  }

  function makeSearchResultRow(m) {
    const active = getActiveWl();
    const inList = active.symbols.includes(m.id);
    const r = {
      price: h('div', { class: 'wl-price' }),
      chg: h('div', { class: 'wl-chg' }),
    };

    const actionBtn = inList
      ? h('span', { class: 'wl-btn-added' }, 'Added ✓')
      : h('button', {
        type: 'button',
        class: 'wl-btn-add',
        on: {
          click: (e) => {
            e.stopPropagation();
            if (!active.symbols.includes(m.id)) {
              active.symbols.push(m.id);
              saveWatchlists(watchlists);
              renderTabs();
              renderControls();
              mount(actionWrap, h('span', { class: 'wl-btn-added' }, 'Added ✓'));
              toast(`Added ${m.label || m.name} to ${active.name}`, 'ok');
            }
          },
        },
      }, '+ Add');

    const actionWrap = h('div', null, actionBtn);

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
      h('div', { class: 'wl-row-actions' },
        h('div', { class: 'wl-numbers' }, r.price, r.chg),
        actionWrap));

    rows[m.id] = r;
    listed.push(m);
    return r.btn;
  }

  function renderActiveWatchlist() {
    rows = {};
    listed = [];
    const active = getActiveWl();
    if (!active.symbols.length) {
      mount(listEl, h('div', { class: 'empty', style: { padding: '40px 16px' } },
        h('strong', null, `${active.name} is empty`),
        'Search for stocks or indices above and tap "+ Add" to include them in this watchlist.'));
      return;
    }

    const items = active.symbols.map((symId) => {
      let m = market(symId);
      if (!m || m.id !== symId) {
        m = { id: symId, name: symId, label: symId, kind: 'equity', lot_size: 1, tick: 0.05, tradable: true };
      }
      return makeWatchlistRow(m);
    });

    mount(listEl, h('div', { class: 'wl-items' }, items));
    paintMarkets();

    // Ensure all symbols have fresh data loaded
    active.symbols.forEach((id) => {
      if (!store.prices[id] || !store.byId[id]) {
        ensureInstrument(id).then(() => paintMarkets()).catch(() => {});
      }
    });
  }

  function renderSearchResults(items) {
    rows = {};
    listed = [];
    if (!items.length) {
      mount(listEl, h('div', { class: 'empty', style: { padding: '40px 16px' } },
        h('strong', null, 'No instruments found'),
        'Try searching for NIFTY, BANKNIFTY, RELIANCE, TCS, or INFY.'));
      return;
    }

    mount(listEl, h('div', { class: 'wl-items' }, items.map((m) => makeSearchResultRow(m))));
    paintMarkets();
  }

  const runSearch = debounce(async () => {
    const q = search.value.trim();
    if (!q) {
      isSearching = false;
      renderControls();
      renderActiveWatchlist();
      return;
    }
    isSearching = true;
    renderControls();
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
      renderSearchResults(r.markets);
    } catch (e) {
      console.error(e);
    }
  }, 220);

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
      h('p', { class: 'page-sub' }, 'Manage your custom watchlists. Tap any stock or index to trade.')),
    tabsWrap,
    h('div', { class: 'panel wl-panel' },
      controlsBar,
      h('div', { class: 'wl-search-bar' }, search),
      listEl)));

  renderTabs();
  renderControls();
  renderActiveWatchlist();

  return function destroy() {
    unsub.forEach((f) => f());
    runSearch.cancel && runSearch.cancel();
  };
}
