// Stats: how the account is really doing, and which habits help or hurt.
import { api } from '../api.js';
import { h, mount, num, money, pct, rMult, tone, arrow, duration, dateTime } from '../util.js';
import { store, on, market } from '../store.js';
import { createEquityChart } from '../chart.js';
import { REASONS } from '../dialogs.js';

const PERIODS = [['all', 'All time'], ['30d', 'Last 30 days'], ['7d', 'Last 7 days']];
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const CLASS_NAMES = {};

function signed(n) { return `${arrow(n)} ${money(n, { sign: true })}`.trim(); }

// Plain-language observations. Each one states a fact about the numbers, nothing more.
export function insights(s) {
  const out = [];
  if (!s.trades || s.trades < 5) return out;
  if (s.with_stop_pct < 80) {
    out.push(`${num(s.with_stop_pct, 0)}% of your trades had a stop-loss. Trades without one have no R-multiple, so they are missing from the R numbers.`);
  }
  if (s.win_rate >= 50 && s.payoff_ratio != null && s.payoff_ratio < 1) {
    out.push(`You win ${num(s.win_rate, 0)}% of your trades, but your average loss (${money(s.avg_loss)}) is bigger than your average win (${money(s.avg_win)}). Winning often does not help when the losses are larger.`);
  }
  if (s.win_rate < 40 && s.payoff_ratio != null && s.payoff_ratio >= 2) {
    out.push(`You win only ${num(s.win_rate, 0)}% of your trades, but your average win is ${num(s.payoff_ratio, 1)} times your average loss. That can work as long as you keep the ratio.`);
  }
  if (s.longest_loss_streak >= 4) {
    out.push(`Your longest losing streak is ${s.longest_loss_streak} trades. Read those trades in the journal and look for what they had in common.`);
  }
  if (s.total_fees > 0 && s.total_pnl + s.total_fees > 0 && s.total_pnl <= 0) {
    out.push(`Before fees you were ahead by ${money(s.total_pnl + s.total_fees)}, but fees of ${money(s.total_fees)} took it below zero. Fewer, better trades cost less.`);
  }
  return out.slice(0, 3);
}

export function renderStats(root) {
  let period = 'all';
  let data = null;
  let failed = '';
  let chart = null;
  let alive = true;
  let req = 0;
  const unsub = [];

  const body = h('div');
  const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Period', style: { maxWidth: '360px' } });
  mount(root, h('div', { class: 'page' },
    h('div', { class: 'page-head' }, h('h1', null, 'Stats'), seg), body));

  function drawSeg() {
    mount(seg, PERIODS.map(([v, label]) => h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(v === period),
      on: { click: () => { if (v !== period) { period = v; drawSeg(); load(); } } },
    }, label)));
  }

  function tile(label, value, sub, cls = '') {
    return h('div', { class: 'panel kpi' }, h('span', null, label), h('strong', { class: cls }, value), sub ? h('small', null, sub) : null);
  }

  function breakdown(titleText, rows, labelFor, emptyHint) {
    const card = h('section', { class: 'panel bd' }, h('h2', null, titleText));
    if (!rows.length) { card.append(h('p', { class: 'fine' }, emptyHint)); return card; }
    const max = Math.max(...rows.map((r) => Math.abs(r.pnl)), 0.01);
    for (const r of rows) {
      const w = Math.max(1, (Math.abs(r.pnl) / max) * 50);
      const label = labelFor(r.key);
      const fill = h('div', { class: `bd-fill ${r.pnl >= 0 ? 'up' : 'down'}`, style: r.pnl >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` } });
      card.append(h('div', { class: 'bd-row', title: `${label}: ${r.trades} trade${r.trades === 1 ? '' : 's'}, ${num(r.win_rate, 0)}% wins, ${money(r.pnl, { sign: true })}` },
        h('span', { class: 'bd-key' }, label),
        h('div', { class: 'bd-bar', 'aria-hidden': 'true' }, fill),
        h('span', { class: `bd-val ${tone(r.pnl)}` }, signed(r.pnl), h('small', null, `${r.trades} trade${r.trades === 1 ? '' : 's'}, ${num(r.win_rate, 0)}% wins`))));
    }
    return card;
  }

  function paint() {
    chart && chart.destroy(); chart = null;
    if (failed) { mount(body, h('div', { class: 'panel empty' }, h('strong', null, 'Could not load your stats'), failed)); return; }
    if (!data) { mount(body, h('p', { class: 'empty' }, 'Loading your stats…')); return; }
    const s = data;
    if (!s.trades) {
      mount(body, h('div', { class: 'panel empty' }, h('strong', null, 'No closed trades in this period'),
        'Close a few trades on the ', h('a', { href: '#/trade' }, 'Trade page'), ' and your numbers will build up here.'));
      return;
    }

    const curve = s.equity_curve || [];
    const startEq = curve.length ? curve[0][1] : store.me.user.start_balance;
    const endEq = curve.length ? curve[curve.length - 1][1] : startEq;
    const change = startEq > 0 ? (endEq / startEq - 1) * 100 : 0;
    const streak = s.current_streak > 0 ? `Now on ${s.current_streak} wins in a row` : s.current_streak < 0 ? `Now on ${-s.current_streak} losses in a row` : 'No active streak';

    const kpis = h('div', { class: 'kpis' },
      tile('Total P&L', signed(s.total_pnl), `Equity ${money(startEq, { decimals: 0 })} → ${money(endEq, { decimals: 0 })} (${pct(change, { sign: true })})`, tone(s.total_pnl)),
      tile('Win rate', pct(s.win_rate, { decimals: 0 }), `${s.wins} won, ${s.losses} lost`),
      tile('Profit factor', s.profit_factor != null ? num(s.profit_factor, 2) : '–', s.profit_factor != null ? 'Money won for each ₹1 lost' : 'No losing trades yet'),
      tile('Expectancy', signed(s.expectancy), s.expectancy_r != null ? `${rMult(s.expectancy_r)} per trade, on average` : 'Needs trades with a stop-loss', tone(s.expectancy)),
      tile('Average win / loss', `${money(s.avg_win)} / ${s.avg_loss != null ? money(s.avg_loss) : '–'}`, s.payoff_ratio != null ? `Wins are ${num(s.payoff_ratio, 2)}× the losses` : 'Needs at least one win and one loss'),
      tile('Biggest fall', s.max_drawdown_pct > 0 ? pct(-s.max_drawdown_pct, { decimals: 1 }) : '0%', 'Largest drop from an equity peak', s.max_drawdown_pct > 0 ? 'down' : ''),
      tile('Closed trades', String(s.trades), `${num(s.with_stop_pct, 0)}% had a stop-loss`),
      tile('Fees paid', money(s.total_fees), 'Already included in P&L'),
      tile('Best / worst trade', `${money(s.best_trade, { sign: true })} / ${money(s.worst_trade, { sign: true })}`),
      tile('Average time open', duration(s.avg_hold_seconds)),
      tile('Longest streaks', `${s.longest_win_streak} / ${s.longest_loss_streak}`, `Wins / losses. ${streak}`),
      tile('Typical swing', `${pct(s.avg_mfe_pct)} / ${pct(s.avg_mae_pct)}`, 'Price moved this much for / against you while a trade was open'));

    const read = h('div', { class: 'eq-read' });
    const box = h('div', { class: 'eq-box' });
    const showLatest = () => mount(read, h('strong', null, money(endEq)), h('span', { class: tone(change) }, `${arrow(change)} ${pct(change, { sign: true })} over this period`.trim()), h('span', { class: 'muted' }, 'Hover the chart to see any point in time'));
    showLatest();

    const tips = insights(s);
    const bySetup = s.by_setup || [], byEmotion = s.by_emotion || [];
    const weekdays = [...(s.by_weekday || [])].sort((a, b) => WEEKDAYS.indexOf(a.key) - WEEKDAYS.indexOf(b.key));

    mount(body,
      h('section', { class: 'panel', style: { marginBottom: '14px', overflow: 'hidden' } },
        h('div', { class: 'panel-pad', style: { paddingBottom: 0 } }, h('h2', null, 'Equity')), read, box),
      kpis,
      tips.length ? h('section', { class: 'panel insights' }, h('h2', null, 'Worth a look'), h('ul', { class: 'explain' }, tips.map((t) => h('li', null, t)))) : null,
      h('div', { class: 'breakdowns' },
        breakdown('By underlying', s.by_symbol, (k) => k, ''),
        breakdown('By asset type', s.by_class, (k) => CLASS_NAMES[k] || k, ''),
        breakdown('By product', s.by_product || [], (k) => ({ CNC: 'CNC (delivery)', MIS: 'MIS (intraday)', NRML: 'NRML (F&O carry)' }[k] || k), ''),
        breakdown('Long or short', s.by_side, (k) => (k === 'long' ? '▲ Long' : '▼ Short'), ''),
        breakdown('By setup', bySetup, (k) => k, 'Choose a setup on each trade (order panel or journal) to compare them here.'),
        breakdown('By how you felt', byEmotion, (k) => k, 'Note how you felt in the journal to see which moods cost you money.'),
        breakdown('How trades ended', s.by_exit, (k) => REASONS[k] || k, ''),
        breakdown('Day you opened', weekdays, (k) => k, '')));

    if (window.LightweightCharts && curve.length > 1) {
      chart = createEquityChart(box, curve, period === 'all' ? store.me.user.start_balance : startEq);
      chart.onHover((p) => {
        if (!p) { showLatest(); return; }
        const delta = p.value - startEq;
        mount(read, h('strong', null, money(p.value)),
          h('span', { class: tone(delta) }, `${arrow(delta)} ${money(delta, { sign: true })} from the start of this period`.trim()),
          h('span', { class: 'muted' }, dateTime(p.time)));
      });
    } else {
      box.append(h('p', { class: 'empty' }, 'Close more trades to draw your equity curve.'));
    }
  }

  async function load() {
    const id = ++req;
    data = null; failed = ''; paint();
    try {
      const r = await api('/api/stats', { params: { period } });
      if (!alive || id !== req) return;
      data = r;
    } catch (e) {
      if (!alive || id !== req) return;
      failed = e.message;
    }
    paint();
  }

  unsub.push(on('theme', () => { if (data) paint(); }));
  drawSeg();
  load();

  return function destroy() { alive = false; unsub.forEach((f) => f()); chart && chart.destroy(); };
}
