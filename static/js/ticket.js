// Order ticket for FirsTrade.
// Redesigned to match the clean Option Chain trade modal layout.
import { api } from './api.js';
import { h, mount, debounce, num, money, pct, price, toast } from './util.js';
import { store, on, emit, market, setAccount } from './store.js';

const SETUPS = ['Breakout', 'Pullback', 'Reversal', 'Range', 'Trend follow', 'News', 'Other'];
const SL_CHIPS_EQUITY = [0.5, 1, 2, 5];
const SL_CHIPS_DERIV = [10, 20, 30];
const TP_CHIPS_EQUITY = [1, 2, 3, 5];
const TP_CHIPS_DERIV = [20, 50, 100];

function parse(v) {
  const n = parseFloat(String(v).replace(/,/g, ''));
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function createTicket(root) {
  const ui = {
    side: 'long',
    product: '',
    type: 'market',
    limit: '',
    qty: 1,
    sl: '',
    tp: '',
    setup: '',
    note: '',
  };
  if (store.pendingSide) { ui.side = store.pendingSide; store.pendingSide = null; }

  let preview = null;
  let previewErr = '';
  let reqId = 0;
  let busy = false;
  const refs = {};
  const unsub = [];

  // ---------------------------------------------------------------- helpers
  const mk = () => market(store.symbol);
  const live = () => { const p = store.prices[store.symbol]; return p && p.f ? p.p : null; };
  const isDeriv = (m) => m && (m.kind === 'future' || m.kind === 'option' || /\d+(CE|PE)$/i.test(m.id) || m.id.endsWith('FUT') || /\d+[A-Z]{3}FUT$/i.test(m.id));
  const marketOpen = () => !store.market || store.market.open !== false;
  const refPrice = () => (ui.type === 'limit' ? parse(ui.limit) : live());

  function fixProduct(m) {
    const list = (m && m.products && m.products.length) ? m.products : (isDeriv(m) ? ['MIS', 'NRML'] : ['CNC', 'MIS']);
    if (!list.includes(ui.product)) {
      ui.product = (ui.side === 'short' && !isDeriv(m)) ? 'MIS' : list[0];
    }
    if (ui.product === 'CNC' && ui.side === 'short') {
      ui.product = 'MIS';
    }
  }

  // ---------------------------------------------------------------- rendering
  function render() {
    const m = mk();
    if (!m) return;
    refs.preview = null;
    const user = store.me.user;

    // Index cannot be traded directly
    if (m.tradable === false) {
      const fno = m.fno;
      const ltp = live();
      mount(root,
        h('div', { class: 'dlg-opt-head', style: { padding: '0 0 12px 0', borderBottom: '1px solid var(--line)' } },
          h('div', { class: 'dlg-opt-title' },
            h('h2', { style: { fontSize: '18px', fontWeight: '700' } }, m.name),
            h('div', { class: 'dlg-opt-meta' }, h('span', { class: 'badge' }, 'INDEX'))),
          h('div', { class: 'dlg-opt-price-box' },
            h('div', null,
              h('span', { class: 'fine' }, 'LTP '),
              h('div', { class: 'dlg-opt-price' }, ltp != null ? price(m, ltp) : '–')))),
        h('div', { class: 'empty', style: { padding: '24px 14px', textAlign: 'center' } },
          h('strong', { style: { display: 'block', fontSize: '15px', marginBottom: '6px' } }, `${m.name} is an Index`),
          'An index cannot be bought or sold directly. Trade its futures or options instead.',
          fno ? h('div', { style: { display: 'grid', gap: '8px', marginTop: '14px' } },
            h('a', { class: 'btn primary', href: `#/chain?u=${encodeURIComponent(m.id)}` }, 'Open Option Chain')) : null));
      return;
    }

    fixProduct(m);
    const deriv = isDeriv(m);
    const defaultLot = m.id.startsWith('BANKNIFTY') ? 30 : (m.id.startsWith('FINNIFTY') ? 60 : (m.id.startsWith('MIDCPNIFTY') ? 120 : (m.id.startsWith('NIFTY') ? 65 : 25)));
    const lotSize = deriv ? ((m.lot_size && m.lot_size > 1) ? m.lot_size : defaultLot) : 1;
    const tickSize = m.tick || 0.05;
    const currentPrice = live();

    // 1. Header with Symbol, Badges and Live LTP
    const metaBadges = [];
    if (!deriv) {
      metaBadges.push(h('span', { class: 'badge' }, 'EQ'));
    } else if (m.kind === 'future') {
      metaBadges.push(h('span', { class: 'badge accent' }, 'FUT'));
    } else if (m.kind === 'option') {
      metaBadges.push(h('span', { class: `badge ${m.option_type === 'CE' ? 'up' : 'down'}` }, m.option_type === 'CE' ? 'CALL' : 'PUT'));
      if (m.strike) metaBadges.push(h('span', { class: 'badge accent' }, `Strike ${num(m.strike, 0)}`));
    }
    if (deriv && lotSize > 1) metaBadges.push(h('span', { class: 'badge' }, `Lot: ${lotSize}`));

    refs.priceEl = h('div', { class: 'dlg-opt-price' }, currentPrice != null ? price(m, currentPrice) : '–');
    const head = h('div', { class: 'dlg-opt-head', style: { padding: '0 0 12px 0', borderBottom: '1px solid var(--line)' } },
      h('div', { class: 'dlg-opt-title' },
        h('h2', { style: { fontSize: '18px', fontWeight: '700' } }, m.label || m.name),
        h('div', { class: 'dlg-opt-meta' }, metaBadges)),
      h('div', { class: 'dlg-opt-price-box' },
        h('div', null, h('span', { class: 'fine' }, 'LTP '), refs.priceEl)));

    // 2. Buy / Sell Toggle
    const buyBtn = h('button', {
      type: 'button', class: 'seg-btn long', 'aria-pressed': String(ui.side === 'long'),
      on: { click: () => { ui.side = 'long'; render(); schedule(); } },
    }, '▲ BUY');
    const sellBtn = h('button', {
      type: 'button', class: 'seg-btn short', 'aria-pressed': String(ui.side === 'short'),
      on: { click: () => {
        ui.side = 'short';
        if (ui.product === 'CNC') ui.product = 'MIS';
        render();
        schedule();
      } },
    }, '▼ SELL');
    const sideSeg = h('div', { class: 'seg side-seg', role: 'group', 'aria-label': 'Order side' }, buyBtn, sellBtn);

    // 3. Product Selector
    const products = (m.products && m.products.length) ? m.products : (deriv ? ['MIS', 'NRML'] : ['CNC', 'MIS']);
    const prodBtns = products.map((p) => h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(ui.product === p),
      on: { click: () => {
        ui.product = p;
        if (ui.product === 'CNC') ui.side = 'long';
        render();
        schedule();
      } },
    }, p));
    const prodSeg = h('div', { class: 'seg prod-seg', role: 'group', 'aria-label': 'Product type' }, prodBtns);
    let prodDesc = '';
    if (ui.product === 'CNC') prodDesc = 'CNC: Delivery product. You pay the full value and can only buy.';
    else if (ui.product === 'MIS') prodDesc = 'MIS: Intraday with ~5× leverage. Auto squared-off at 3:20 PM.';
    else if (ui.product === 'NRML') prodDesc = 'NRML: Carry forward overnight until expiry date.';
    const prodNote = h('p', { class: 'prod-note' }, prodDesc);

    // 4. Order Type Selector (Market / Limit)
    const mktBtn = h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(ui.type === 'market'),
      on: { click: () => { ui.type = 'market'; render(); schedule(); } },
    }, 'Market');
    const lmtBtn = h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(ui.type === 'limit'),
      on: { click: () => {
        ui.type = 'limit';
        if (!ui.limit && currentPrice != null) ui.limit = (Math.round(currentPrice / tickSize) * tickSize).toFixed(2);
        render();
        schedule();
      } },
    }, 'Limit');
    const typeSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Order type' }, mktBtn, lmtBtn);

    // Limit price stepper
    const limitInput = h('input', {
      type: 'text', inputmode: 'decimal', placeholder: 'Limit price', value: ui.limit, class: 'stepper-input'
    });
    limitInput.addEventListener('input', () => { ui.limit = limitInput.value; schedule(); });
    const limitDec = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
      const cur = parseFloat(ui.limit) || currentPrice || 0;
      ui.limit = Math.max(0.05, cur - tickSize).toFixed(2);
      limitInput.value = ui.limit;
      schedule();
    } } }, '−');
    const limitInc = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
      const cur = parseFloat(ui.limit) || currentPrice || 0;
      ui.limit = (cur + tickSize).toFixed(2);
      limitInput.value = ui.limit;
      schedule();
    } } }, '+');
    const limitField = h('div', { class: 'field', style: { display: ui.type === 'limit' ? 'grid' : 'none' } },
      h('span', { class: 'field-label' }, 'Limit Price (₹)'),
      h('div', { class: 'stepper' }, limitDec, limitInput, limitInc),
      h('span', { class: 'field-hint' }, ui.side === 'long' ? 'Must be below current LTP for limit buy' : 'Must be above current LTP for limit sell'));

    // 5. Quantity Stepper & Chips
    const qtyChipsValues = deriv ? [1, 2, 5, 10] : [1, 5, 10, 25, 50, 100];
    const qtyInput = h('input', {
      type: 'text', inputmode: 'numeric', value: String(ui.qty), class: 'stepper-input'
    });
    qtyInput.addEventListener('input', () => {
      const n = parseInt(qtyInput.value.replace(/\D/g, ''), 10);
      ui.qty = Number.isInteger(n) && n > 0 ? Math.min(n, 100000) : 1;
      renderQuantityText();
      schedule();
    });
    const qtyDec = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
      if (ui.qty > 1) { ui.qty -= 1; qtyInput.value = String(ui.qty); renderQuantityText(); schedule(); }
    } } }, '−');
    const qtyInc = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
      ui.qty += 1; qtyInput.value = String(ui.qty); renderQuantityText(); schedule();
    } } }, '+');
    const qtyChips = qtyChipsValues.map((n) => h('button', {
      type: 'button', class: 'chip',
      on: { click: () => { ui.qty = n; qtyInput.value = String(n); renderQuantityText(); schedule(); } },
    }, deriv ? `${n} Lot${n > 1 ? 's' : ''}` : String(n)));
    const qtyHint = h('span', { class: 'field-hint', style: { fontWeight: '600' } });

    function renderQuantityText() {
      if (deriv) {
        qtyHint.textContent = `Total: ${num(ui.qty * lotSize, 0)} shares (${ui.qty} lot${ui.qty > 1 ? 's' : ''} × ${lotSize})`;
      } else {
        qtyHint.textContent = `Total: ${num(ui.qty, 0)} share${ui.qty > 1 ? 's' : ''}`;
      }
    }
    renderQuantityText();

    const qtyField = h('div', { class: 'field' },
      h('div', { class: 'field-row' },
        h('span', { class: 'field-label' }, deriv ? 'Quantity (Lots)' : 'Quantity (Shares)'),
        h('div', { class: 'chips' }, qtyChips)),
      h('div', { class: 'stepper' }, qtyDec, qtyInput, qtyInc),
      qtyHint);

    // 6. Stop-loss & Target (Collapsible)
    const slPercents = deriv ? SL_CHIPS_DERIV : SL_CHIPS_EQUITY;
    const tpPercents = deriv ? TP_CHIPS_DERIV : TP_CHIPS_EQUITY;

    const slIn = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'Stop-loss price (optional)', value: ui.sl });
    slIn.addEventListener('input', () => { ui.sl = slIn.value; schedule(); });
    refs.sl = slIn;
    const slChips = slPercents.map((p) => h('button', {
      type: 'button', class: 'chip',
      on: { click: () => {
        const ref = refPrice();
        if (!ref) { toast('Wait for live price or enter limit price.', 'error'); return; }
        const v = ui.side === 'long' ? ref * (1 - p / 100) : ref * (1 + p / 100);
        ui.sl = (Math.round(v / tickSize) * tickSize).toFixed(2);
        slIn.value = ui.sl;
        schedule();
      } },
    }, `−${p}%`));

    const tpIn = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'Target price (optional)', value: ui.tp });
    tpIn.addEventListener('input', () => { ui.tp = tpIn.value; schedule(); });
    refs.tp = tpIn;
    const tpChips = tpPercents.map((p) => h('button', {
      type: 'button', class: 'chip',
      on: { click: () => {
        const ref = refPrice();
        if (!ref) { toast('Wait for live price or enter limit price.', 'error'); return; }
        const v = ui.side === 'long' ? ref * (1 + p / 100) : ref * (1 - p / 100);
        ui.tp = (Math.round(v / tickSize) * tickSize).toFixed(2);
        tpIn.value = ui.tp;
        schedule();
      } },
    }, `+${p}%`));

    const targetsWrap = h('details', { class: 'journal-fold' },
      h('summary', null, 'Stop-loss & Target (optional)'),
      h('div', { style: { display: 'grid', gap: '10px', padding: '10px 12px 14px' } },
        h('div', { class: 'field' },
          h('div', { class: 'field-row' }, h('span', { class: 'field-label' }, 'Stop-loss price'), h('div', { class: 'chips' }, slChips)),
          slIn),
        h('div', { class: 'field' },
          h('div', { class: 'field-row' }, h('span', { class: 'field-label' }, 'Target price'), h('div', { class: 'chips' }, tpChips)),
          tpIn)));

    // 7. Preview Box
    refs.preview = h('div', { class: 'opt-preview-card' });

    // 8. Submit button & messages
    refs.submit = h('button', { type: 'button', class: `btn submit ${ui.side}`, on: { click: submit } });
    refs.msg = h('p', { class: 'ticket-msg', role: 'status' });

    // 9. Journal section (Optional)
    const setupSel = h('select', { 'aria-label': 'Setup' },
      h('option', { value: '' }, 'No setup'),
      SETUPS.map((s) => h('option', { value: s, selected: ui.setup === s }, s)));
    setupSel.addEventListener('change', () => { ui.setup = setupSel.value; });
    const noteIn = h('input', { type: 'text', maxlength: '500', placeholder: 'Why this trade?', value: ui.note });
    noteIn.addEventListener('input', () => { ui.note = noteIn.value; });
    const journalWrap = h('details', { class: 'journal-fold' },
      h('summary', null, 'Journal notes (optional)'),
      h('div', { class: 'journal-fields', style: { display: 'grid', gap: '8px', padding: '10px 12px 14px' } }, setupSel, noteIn));

    // Mount full ticket
    mount(root,
      head,
      sideSeg,
      h('div', { class: 'field' },
        h('span', { class: 'field-label' }, 'Product'),
        prodSeg,
        prodNote),
      h('div', { class: 'field' },
        h('span', { class: 'field-label' }, 'Order Type'),
        typeSeg),
      limitField,
      qtyField,
      targetsWrap,
      refs.preview,
      journalWrap,
      refs.submit,
      refs.msg,
      h('p', { class: 'fine', style: { fontSize: '12px', color: 'var(--ink-3)', margin: '4px 0 0' } },
        user && user.settings && user.settings.require_sl ? 'Your rules require a stop-loss on every trade.' : 'Tip: set a stop-loss to manage risk and track R-multiples.'));

    renderSubmit();
    renderPreview();
  }

  // ---------------------------------------------------------------- preview
  const schedule = debounce(fetchPreview, 200);

  function params() {
    const m = mk();
    if (!m || m.tradable === false || !ui.product) return null;
    const deriv = isDeriv(m);
    const q = { symbol: m.id, side: ui.side, product: ui.product };

    if (ui.type === 'limit') {
      const entry = parse(ui.limit);
      if (!entry) return null;
      q.entry = entry;
    }
    const sl = parse(ui.sl), tp = parse(ui.tp);
    if (sl) q.stop = sl;
    if (tp) q.tp = tp;

    const n = ui.qty;
    if (!n || !Number.isInteger(n) || n <= 0) return null;
    if (deriv) q.lots = n; else q.qty = n;

    return q;
  }

  async function fetchPreview() {
    const q = params();
    emitDraft();
    if (!q) {
      preview = null; previewErr = '';
      renderPreview(); renderSubmit();
      return;
    }
    const mine = ++reqId;
    try {
      const r = await api('/api/risk/size', { params: q });
      if (mine !== reqId) return;
      preview = r; previewErr = '';
    } catch (e) {
      if (mine !== reqId) return;
      preview = null; previewErr = e.message;
    }
    renderPreview(); renderSubmit(); emitDraft();
  }

  function emitDraft() {
    const m = mk();
    if (!m) return;
    emit('draft', {
      symbol: m.id, side: ui.side,
      sl: parse(ui.sl), tp: parse(ui.tp),
      entry: ui.type === 'limit' ? parse(ui.limit) : null,
      liq: preview ? preview.liq_price : null,
    });
  }

  function renderPreview() {
    if (!refs.preview) return;
    const m = mk();
    if (!preview) {
      mount(refs.preview,
        previewErr
          ? h('span', { class: 'opt-preview-warn' }, previewErr)
          : h('span', { class: 'fine' }, 'Enter quantity to preview margin and charges.'));
      return;
    }
    const p = preview;
    const deriv = isDeriv(m);
    mount(refs.preview,
      h('div', { class: 'opt-preview-row' },
        h('span', null, 'Margin needed'),
        h('strong', null, `${money(p.margin)} (Free: ${money(Math.max(p.available, 0))})`)),
      h('div', { class: 'opt-preview-row' },
        h('span', null, deriv ? 'Contract value' : 'Order value'),
        h('strong', null, money(p.value))),
      h('div', { class: 'opt-preview-row' },
        h('span', null, 'Charges to open'),
        h('strong', null, money(p.fees_open, { decimals: 2 }))),
      p.risk != null ? h('div', { class: 'opt-preview-row' },
        h('span', null, 'Risk at stop'),
        h('strong', null, `${money(p.risk)} (${pct(p.risk_pct)})`)) : null,
      p.rr != null ? h('div', { class: 'opt-preview-row' },
        h('span', null, 'Reward : risk'),
        h('strong', null, `${num(p.rr, 2)} : 1`)) : null,
      p.liq_price != null ? h('div', { class: 'opt-preview-row' },
        h('span', null, 'Auto-exit near'),
        h('strong', null, price(m, p.liq_price))) : null,
      p.warnings && p.warnings.length
        ? h('div', { class: 'opt-preview-warn' }, p.warnings.join(' · '))
        : null);
  }

  // ---------------------------------------------------------------- submit
  function canSubmit() {
    const m = mk();
    if (busy || !m || m.tradable === false) return false;
    if (!live() || !marketOpen()) return false;
    const a = store.account;
    if (a && a.locked) return false;
    return !!preview && preview.qty > 0 && preview.warnings.every((w) => !/Not enough free margin|Minimum size/.test(w));
  }

  function renderSubmit() {
    const b = refs.submit;
    if (!b) return;
    const m = mk();
    if (!m) return;
    const deriv = isDeriv(m);
    const verb = ui.type === 'limit' ? (ui.side === 'long' ? 'BUY LIMIT' : 'SELL LIMIT') : (ui.side === 'long' ? 'BUY' : 'SELL');
    const qtyText = deriv ? `${ui.qty} LOT${ui.qty > 1 ? 'S' : ''}` : `${ui.qty} QTY`;
    b.textContent = busy ? 'Placing order…' : `${verb} ${qtyText} · ${m.label || m.name}`;
    b.className = `btn submit ${ui.side}`;
    b.disabled = !canSubmit();

    const acct = store.account;
    let msg = '';
    if (!marketOpen()) {
      const mm = store.market || {};
      msg = `The market is closed. Orders are accepted 9:15 am to 3:30 pm India time, Monday to Friday${mm.next_open ? ` (next open: ${new Date(mm.next_open).toLocaleString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' })})` : ''}.`;
    } else if (!live()) {
      msg = `${m.name} has no fresh price right now. Try again shortly.`;
    } else if (acct && acct.locked) {
      msg = 'Daily loss limit reached. New trades unlock on the next trading day.';
    }
    if (refs.msg) refs.msg.textContent = msg;
  }

  async function submit() {
    if (!canSubmit()) return;
    const m = mk();
    const deriv = isDeriv(m);
    const body = {
      symbol: m.id, side: ui.side, product: ui.product, type: ui.type,
      sl: parse(ui.sl), tp: parse(ui.tp), setup: ui.setup, note: ui.note,
    };
    if (ui.type === 'limit') body.limit_price = parse(ui.limit);
    if (deriv) body.lots = ui.qty; else body.qty = ui.qty;

    busy = true; renderSubmit();
    try {
      const r = await api('/api/orders', { method: 'POST', body });
      setAccount(r.account);
      const name = m.label || m.name;
      const unitText = deriv ? `${ui.qty} lot${ui.qty > 1 ? 's' : ''}` : `${ui.qty} shares`;
      toast(r.result.kind === 'order' ? `Limit order placed for ${unitText} of ${name}.` : `${ui.side === 'long' ? 'Bought' : 'Sold'} ${unitText} of ${name}!`, 'ok');
      ui.sl = ''; ui.tp = ''; ui.note = ''; ui.qty = 1;
      preview = null;
      render();
      emitDraft();
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      busy = false; renderSubmit();
    }
  }

  // ---------------------------------------------------------------- wiring
  unsub.push(on('symbol', () => {
    ui.sl = ''; ui.tp = ''; ui.limit = ''; ui.qty = 1;
    if (store.pendingSide) { ui.side = store.pendingSide; store.pendingSide = null; }
    ui.product = '';
    preview = null; previewErr = '';
    render(); schedule();
  }));
  unsub.push(on('account', () => { renderSubmit(); }));
  unsub.push(on('market', () => { renderSubmit(); }));
  let lastLive = null;
  let lastRefresh = 0;
  unsub.push(on('prices', () => {
    const l = live();
    if (refs.priceEl && l != null) {
      const m = mk();
      if (m) refs.priceEl.textContent = price(m, l);
    }
    if ((l === null) !== (lastLive === null)) renderSubmit();
    lastLive = l;
    if (ui.type === 'market' && params() && Date.now() - lastRefresh > 4000) {
      lastRefresh = Date.now();
      schedule();
    }
  }));

  render();
  schedule();

  return {
    destroy() { schedule.cancel(); unsub.forEach((f) => f()); emit('draft', null); mount(root); },
    setSide(side) {
      if (side !== 'long' && side !== 'short') return;
      const m = mk();
      if (m && m.products && !isDeriv(m) && side === 'short' && ui.product === 'CNC') {
        ui.product = 'MIS';
      }
      ui.side = side;
      render();
      schedule();
    },
    setField(kind, value) {
      if (!refs.sl) return;
      const v = (Math.round(Number(value) / 0.05) * 0.05).toFixed(2);
      if (kind === 'sl') { ui.sl = v; refs.sl.value = v; }
      if (kind === 'tp') { ui.tp = v; refs.tp.value = v; }
      schedule();
    },
  };
}
