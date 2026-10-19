// Option chain: calls on the left, puts on the right, strikes in the middle.
// Click a price to buy or sell that contract on the Trade page.
import { api } from '../api.js';
import { h, mount, num, price, toast, storage } from '../util.js';
import { store, setSymbol, ensureInstrument } from '../store.js';
import { openOptionOrderDialog } from '../dialogs.js';

const STRIKE_CHOICES = [5, 8, 12, 20];

function queryParam(name) {
  const q = (location.hash.split('?')[1] || '');
  return new URLSearchParams(q).get(name);
}

function fmtOi(v) {
  if (v == null) return '–';
  if (v >= 1e7) return `${num(v / 1e7, 2)} Cr`;
  if (v >= 1e5) return `${num(v / 1e5, 2)} L`;
  return num(v, 0);
}

export function renderChain(root) {
  let alive = true;
  let timer = null;
  let expiries = null;
  let data = null;
  let failed = '';
  let busy = false;

  const unders = store.markets.filter((m) => m.fno);
  const wanted = queryParam('u');
  const saved = storage('zp_chain_u');
  const state = {
    under: [wanted, saved, 'NIFTY'].find((u) => u && unders.some((m) => m.id === u)) || (unders[0] && unders[0].id) || 'NIFTY',
    expiry: '',
    strikes: Number(storage('zp_chain_n')) || 8,
  };

  const underSel = h('select', { id: 'c-und', 'aria-label': 'Underlying' },
    unders.map((m) => h('option', { value: m.id, selected: m.id === state.under }, m.label || m.name)));
  const expSel = h('select', { id: 'c-exp', 'aria-label': 'Expiry date' });
  const nSel = h('select', { id: 'c-n', 'aria-label': 'Strikes shown around the money' },
    STRIKE_CHOICES.map((n) => h('option', { value: String(n), selected: n === state.strikes }, `${n} each side`)));
  const metaEl = h('div', { class: 'chain-meta', 'aria-live': 'polite' });
  const tableWrap = h('div', { class: 'chain-wrap panel' });
  const noteEl = h('p', { class: 'fine' });

  mount(root, h('div', { class: 'page' },
    h('div', { class: 'page-head' }, h('h1', null, 'Option chain')),
    h('p', { class: 'page-sub' }, 'Pick an underlying and an expiry. Click any option price to buy or sell directly.'),
    h('div', { class: 'chain-bar' },
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Underlying'), underSel),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Expiry'), expSel),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Strikes'), nSel)),
    metaEl,
    tableWrap,
    noteEl));

  function ltpCell(leg, label, itm, strike, optType) {
    if (!leg || !leg.id) return h('td', { class: itm ? 'itm' : '' }, '–');
    return h('td', { class: itm ? 'itm' : '' }, h('button', {
      type: 'button', class: 'ltp-btn', 'aria-label': `${label}, price ${leg.ltp != null ? leg.ltp : 'unknown'}. Tap to trade`,
      on: {
        click: () => openOptionOrderDialog({
          leg,
          label,
          underlying: state.under,
          expiry: state.expiry,
          strike,
          type: optType,
          defaultSide: 'long',
        })
      },
    }, leg.ltp != null ? num(leg.ltp, 2) : '–'));
  }

  function cell(v, dec, cls = '') { return h('td', { class: cls }, v == null ? '–' : num(v, dec)); }

  function paint() {
    if (failed && !data) {
      mount(tableWrap, h('div', { class: 'empty' }, h('strong', null, 'The option chain is not available'), failed,
        h('div', { style: { marginTop: '10px' } }, h('button', { type: 'button', class: 'btn sm', on: { click: refresh } }, 'Try again'))));
      mount(metaEl);
      return;
    }
    if (!data) { mount(tableWrap, h('p', { class: 'empty' }, 'Loading the option chain…')); return; }
    const s = data.summary || {};
    const spot = data.underlying_ltp != null ? data.underlying_ltp : data.underlying_price;
    mount(metaEl,
      h('span', null, `${state.under} `, h('strong', null, spot != null ? num(spot, 2) : '–')),
      h('span', null, 'At the money ', h('strong', null, s.atm != null ? num(s.atm, 0) : '–')),
      h('span', null, 'PCR ', h('strong', null, s.pcr != null ? num(s.pcr, 2) : '–')),
      h('span', null, 'Max pain ', h('strong', null, s.max_pain != null ? num(s.max_pain, 0) : '–')),
      h('span', null, 'Lot ', h('strong', null, num(data.lot_size, 0))),
      h('span', { class: 'muted' }, `Source: ${data.source}`));
    const head = h('thead', null,
      h('tr', { class: 'grp' }, h('th', { class: 'group-head', colspan: '4' }, 'CALLS'), h('th', { class: 'group-head' }, ''), h('th', { class: 'group-head', colspan: '4' }, 'PUTS')),
      h('tr', null,
        h('th', { class: 'hide-xs' }, 'OI'), h('th', { class: 'hide-xs' }, 'IV %'), h('th', { class: 'hide-xs' }, 'Delta'), h('th', null, 'Call price'),
        h('th', { style: { textAlign: 'center' } }, 'Strike'),
        h('th', null, 'Put price'), h('th', { class: 'hide-xs' }, 'Delta'), h('th', { class: 'hide-xs' }, 'IV %'), h('th', { class: 'hide-xs' }, 'OI')));
    const body = h('tbody', null, data.rows.map((r) => {
      const ce = r.ce || {}, pe = r.pe || {};
      const atm = r.strike === s.atm;
      return h('tr', { class: atm ? 'atm' : '' },
        h('td', { class: `hide-xs ${spot != null && r.strike < spot ? 'itm' : ''}` }, fmtOi(ce.oi)),
        cell(ce.iv, 1, `hide-xs ${spot != null && r.strike < spot ? 'itm' : ''}`),
        cell(ce.delta, 2, `hide-xs ${spot != null && r.strike < spot ? 'itm' : ''}`),
        ltpCell(ce, `${state.under} ${num(r.strike, 0)} CE`, spot != null && r.strike < spot, r.strike, 'CE'),
        h('td', { class: 'strike' }, num(r.strike, 0)),
        ltpCell(pe, `${state.under} ${num(r.strike, 0)} PE`, spot != null && r.strike > spot, r.strike, 'PE'),
        cell(pe.delta, 2, `hide-xs ${spot != null && r.strike > spot ? 'itm' : ''}`),
        cell(pe.iv, 1, `hide-xs ${spot != null && r.strike > spot ? 'itm' : ''}`),
        h('td', { class: `hide-xs ${spot != null && r.strike > spot ? 'itm' : ''}` }, fmtOi(pe.oi)));
    }));
    mount(tableWrap, h('table', { class: 'chain' }, head, body));
    noteEl.textContent = data.source === 'demo'
      ? 'Demo data: prices are simulated and the Greeks come from a Black-Scholes model. Shaded cells are in the money.'
      : 'Shaded cells are in the money. Option data can lag by a few seconds and Greeks may be calculated, not exchange values.';
  }

  async function loadExpiries() {
    try {
      expiries = await api('/api/expiries', { params: { underlying: state.under } });
    } catch (e) { failed = e.message; paint(); return false; }
    const list = expiries.options;
    if (!list.includes(state.expiry)) state.expiry = list[0] || '';
    mount(expSel, list.map((d, i) => h('option', { value: d, selected: d === state.expiry },
      `${new Date(`${d}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short' })}${i === 0 ? ' (nearest)' : ''}`)));
    return !!state.expiry;
  }

  async function refresh() {
    if (busy || !alive || !state.expiry) return;
    busy = true;
    try {
      const d = await api('/api/chain', { params: { underlying: state.under, expiry: state.expiry, strikes: state.strikes } });
      if (!alive) return;
      data = d; failed = '';
    } catch (e) {
      if (!alive) return;
      failed = e.message;
    } finally { busy = false; }
    paint();
  }

  async function reload() {
    data = null; failed = ''; paint();
    if (await loadExpiries()) await refresh();
  }

  underSel.addEventListener('change', () => { state.under = underSel.value; state.expiry = ''; storage('zp_chain_u', state.under); reload(); });
  expSel.addEventListener('change', () => { state.expiry = expSel.value; data = null; paint(); refresh(); });
  nSel.addEventListener('change', () => { state.strikes = Number(nSel.value); storage('zp_chain_n', String(state.strikes)); data = null; paint(); refresh(); });

  paint();
  reload();
  timer = setInterval(refresh, 3000);

  return function destroy() {
    alive = false;
    clearInterval(timer);
  };
}
