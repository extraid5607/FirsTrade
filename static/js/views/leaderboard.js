// Leaderboard: ranked by return, for everyone with enough closed trades.
import { api } from '../api.js';
import { h, mount, money, pct, num, tone, arrow } from '../util.js';
import { store } from '../store.js';

const PERIODS = [['all', 'All time'], ['30d', 'Last 30 days'], ['7d', 'Last 7 days']];

export function renderLeaderboard(root) {
  let period = 'all';
  let alive = true;
  let req = 0;

  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Period', style: { maxWidth: '360px' } });
  const body = h('div');
  const note = h('p', { class: 'page-sub' });
  mount(root, h('div', { class: 'page page-narrow' },
    h('div', { class: 'page-head' }, h('h1', null, 'Leaderboard'), seg), note, body));

  function drawSeg() {
    mount(seg, PERIODS.map(([v, label]) => h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(v === period),
      on: { click: () => { if (v !== period) { period = v; drawSeg(); load(); } } },
    }, label)));
  }

  function paint(r) {
    const min = r.min_trades;
    note.textContent = `Ranked by return on your practice account. You appear after ${min} closed trade${min === 1 ? '' : 's'}. Resetting your account starts you again from zero.`;
    if (!r.rows.length) {
      mount(body, h('div', { class: 'panel empty' }, h('strong', null, 'Nobody is ranked yet'),
        `Close ${min} trade${min === 1 ? '' : 's'} to be the first name on the board.`));
      return;
    }
    const me = store.me && store.me.user ? store.me.user.username : null;
    mount(body, h('div', { class: 'panel', style: { overflow: 'hidden' } },
      h('div', { style: { overflowX: 'auto' } },
        h('table', { class: 'tbl tbl-cards' },
          h('thead', null, h('tr', null, [['#', ''], ['Trader', ''], ['Return', 'r'], ['Equity', 'r'], ['Closed trades', 'r'], ['Win rate', 'r'], ['Biggest fall', 'r']].map(([t, c]) => h('th', { class: c }, t)))),
          h('tbody', null, r.rows.map((row) => h('tr', { class: row.username === me ? 'me' : '' },
            h('td', { 'data-label': 'Rank' }, String(row.rank)),
            h('td', { class: 'sym', 'data-label': 'Trader' }, row.username, row.username === me ? h('span', { class: 'badge accent', style: { marginLeft: '8px' } }, 'You') : null),
            h('td', { class: `r ${tone(row.return_pct)}`, 'data-label': 'Return' }, `${arrow(row.return_pct)} ${pct(row.return_pct, { sign: true })}`.trim()),
            h('td', { class: 'r', 'data-label': 'Equity' }, money(row.equity)),
            h('td', { class: 'r', 'data-label': 'Closed trades' }, String(row.trades)),
            h('td', { class: 'r', 'data-label': 'Win rate' }, `${num(row.win_rate, 0)}%`),
            h('td', { class: 'r', 'data-label': 'Biggest fall' }, row.max_drawdown_pct > 0 ? pct(-row.max_drawdown_pct, { decimals: 1 }) : '0%')))))),
    ));
  }

  async function load() {
    const id = ++req;
    mount(body, h('p', { class: 'empty' }, 'Loading the leaderboard…'));
    try {
      const r = await api('/api/leaderboard', { params: { period } });
      if (!alive || id !== req) return;
      paint(r);
    } catch (e) {
      if (!alive || id !== req) return;
      mount(body, h('div', { class: 'panel empty' }, h('strong', null, 'Could not load the leaderboard'), e.message));
    }
  }

  drawSeg();
  load();
  return function destroy() { alive = false; };
}
