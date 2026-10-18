// Journal: every closed trade of the current season, with notes, setup, emotion and rating.
import { api } from '../api.js';
import { h, mount, money, pct, rMult, price, sizeText, tone, arrow, dateTime, duration, toast } from '../util.js';
import { store, market } from '../store.js';
import { openJournalDialog, SETUPS, REASONS } from '../dialogs.js';

const PAGE = 100;

export function renderJournal(root) {
  let trades = [];
  let total = 0;
  let loading = true;
  let failed = '';
  const filter = { symbol: '', result: '', setup: '' };
  let alive = true;

  const head = h('div', { class: 'page-head' }, h('h1', null, 'Journal'));
  const filtersEl = h('div', { class: 'filters' });
  const listEl = h('div', { class: 'jlist' });
  const moreEl = h('div', { style: { display: 'flex', justifyContent: 'center', marginTop: '14px' } });
  mount(root, h('div', { class: 'page page-narrow' }, head,
    h('p', { class: 'page-sub' }, 'Write down why you took each trade and what you learned. Reviewing losing trades teaches you more than counting winners.'),
    filtersEl, h('div', { style: { height: '12px' } }), listEl, moreEl));

  function select(label, key, options) {
    const el = h('select', { 'aria-label': label },
      options.map(([v, t]) => h('option', { value: v, selected: filter[key] === v }, t)));
    el.addEventListener('change', () => { filter[key] = el.value; paintList(); });
    return el;
  }

  function paintFilters() {
    const symbols = [...new Set(trades.map((t) => t.symbol))];
    mount(filtersEl,
      select('Instrument', 'symbol', [['', 'All instruments'], ...symbols.map((s) => [s, (trades.find((t) => t.symbol === s) || {}).label || s])]),
      select('Result', 'result', [['', 'Wins and losses'], ['win', 'Winners'], ['loss', 'Losers']]),
      select('Setup', 'setup', [['', 'All setups'], ...SETUPS.map((s) => [s, s]), ['none', 'No setup']]),
      h('span', { class: 'muted', style: { marginLeft: 'auto' } }, total ? `${total} closed trade${total === 1 ? '' : 's'}` : ''));
  }

  function matches(t) {
    if (filter.symbol && t.symbol !== filter.symbol) return false;
    if (filter.result === 'win' && !(t.pnl > 0)) return false;
    if (filter.result === 'loss' && !(t.pnl < 0)) return false;
    if (filter.setup === 'none' && t.setup) return false;
    if (filter.setup && filter.setup !== 'none' && t.setup !== filter.setup) return false;
    return true;
  }

  function card(t) {
    const m = market(t.symbol);
    const stars = t.rating ? h('span', { class: 'stars', role: 'img', 'aria-label': `Plan rating ${t.rating} out of 5` }, '★'.repeat(t.rating) + '☆'.repeat(5 - t.rating)) : null;
    return h('article', { class: 'panel jcard' },
      h('div', { class: 'jcard-top' },
        h('span', { class: 'sym' }, t.label || m.name),
        h('span', { class: `badge ${t.side === 'long' ? 'up' : 'down'}` }, t.side === 'long' ? '▲ Buy' : '▼ Sell'),
        h('span', { class: 'badge' }, t.product),
        h('span', { class: `badge ${t.reason === 'rms' ? 'down' : ''}` }, REASONS[t.reason] || t.reason),
        h('span', { class: 'muted' }, dateTime(t.closed_at)),
        h('div', { class: `jcard-pnl ${tone(t.pnl)}` },
          `${arrow(t.pnl)} ${money(t.pnl, { sign: true })}`.trim(),
          t.r != null ? h('div', { class: 'fine' }, rMult(t.r)) : h('div', { class: 'fine' }, 'No stop, no R'))),
      h('div', { class: 'jcard-meta' },
        h('span', null, `${price(m, t.entry)} → ${price(m, t.exit)}`),
        h('span', null, sizeText(t)),
        h('span', null, `Held ${duration(t.closed_at - t.opened_at)}`),
        h('span', null, `Fees ${money(t.fee_open + t.fee_close)}`),
        h('span', null, `${pct(t.mfe_pct)} your way, ${pct(t.mae_pct)} against`)),
      (t.setup || t.emotion || stars || t.tags.length)
        ? h('div', { class: 'taglist' },
          t.setup ? h('span', { class: 'badge accent' }, t.setup) : null,
          t.emotion ? h('span', { class: 'badge' }, `Felt ${t.emotion.toLowerCase()}`) : null,
          stars,
          t.tags.map((x) => h('span', { class: 'badge' }, x)))
        : null,
      t.note ? h('p', { class: 'jcard-note' }, t.note) : null,
      t.lesson ? h('p', { class: 'jcard-lesson' }, h('strong', null, 'Lesson: '), t.lesson) : null,
      h('div', null, h('button', { type: 'button', class: 'btn sm', on: { click: () => openJournalDialog(t, (nt) => { Object.assign(t, nt); paintList(); }) } },
        t.note || t.lesson || t.setup || t.emotion ? 'Edit journal' : 'Add journal')));
  }

  function paintList() {
    if (loading) { mount(listEl, h('p', { class: 'empty' }, 'Loading your trades…')); return; }
    if (failed) { mount(listEl, h('div', { class: 'empty' }, h('strong', null, 'Could not load your trades'), failed)); return; }
    if (!trades.length) {
      mount(listEl, h('div', { class: 'panel empty' }, h('strong', null, 'No closed trades yet'),
        'Open a trade on the ', h('a', { href: '#/trade' }, 'Trade page'), ', close it, and it will appear here so you can write down what you learned.'));
      mount(moreEl);
      return;
    }
    const shown = trades.filter(matches);
    mount(listEl, shown.length ? shown.map(card)
      : h('div', { class: 'panel empty' }, h('strong', null, 'No trades match these filters'), 'Change a filter to see more of your trades.'));
    mount(moreEl, trades.length < total
      ? h('button', { type: 'button', class: 'btn', on: { click: loadMore } }, `Load ${Math.min(PAGE, total - trades.length)} more`)
      : null);
  }

  async function loadMore() {
    try {
      const r = await api('/api/trades', { params: { limit: PAGE, offset: trades.length } });
      if (!alive) return;
      trades = trades.concat(r.trades);
      total = r.total;
      paintFilters(); paintList();
    } catch (e) { toast(e.message, 'error'); }
  }

  (async () => {
    try {
      const r = await api('/api/trades', { params: { limit: PAGE } });
      if (!alive) return;
      trades = r.trades; total = r.total;
    } catch (e) {
      if (!alive) return;
      failed = e.message;
    }
    loading = false;
    paintFilters(); paintList();
  })();
  paintFilters(); paintList();

  return function destroy() { alive = false; };
}
