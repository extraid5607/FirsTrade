// Modal dialogs shared by several views. They use the native <dialog> element, so Escape,
// focus trapping and the backdrop come from the browser.
import { api } from './api.js';
import { h, mount, num, money, pct, rMult, price, qtyText, tone, arrow, toast, debounce } from './util.js';
import { store, market, setAccount, ensureInstrument } from './store.js';

export const SETUPS = ['Breakout', 'Pullback', 'Reversal', 'Range', 'Trend follow', 'News', 'Other'];
export const EMOTIONS = ['Calm', 'Confident', 'Anxious', 'Greedy', 'FOMO', 'Revenge', 'Bored', 'Tired'];
export const REASONS = { manual: 'Closed by you', sl: 'Stop-loss', tp: 'Target hit', squareoff: 'Auto square-off', rms: 'Risk-system exit', expiry: 'Expired', reset: 'Account reset' };

let seq = 0;

function openDialog(dlg) {
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });   // click on the backdrop
  dlg.addEventListener('close', () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

function parseOptional(text) {
  const t = String(text).trim().replace(/,/g, '');
  if (t === '') return { value: null };
  const n = Number(t);
  if (!Number.isFinite(n) || n <= 0) return { error: true };
  return { value: n };
}

// ---------------------------------------------------------------- confirm
export function confirmDialog({ title, body, confirmLabel = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const id = `dlg-${++seq}`;
    let result = false;
    const dlg = h('dialog', { 'aria-labelledby': id },
      h('div', { class: 'dlg-head' }, h('h2', { id }, title)),
      h('div', { class: 'dlg-body' }, h('p', null, body)),
      h('div', { class: 'dlg-foot' },
        h('button', { type: 'button', class: 'btn', on: { click: () => dlg.close() } }, 'Cancel'),
        h('button', { type: 'button', class: `btn ${danger ? 'danger solid' : 'primary'}`, on: { click: () => { result = true; dlg.close(); } } }, confirmLabel)));
    dlg.addEventListener('close', () => resolve(result));
    openDialog(dlg);
  });
}

// ---------------------------------------------------------------- edit stop-loss / take-profit
export function openStopsDialog(pos) {
  const m = market(pos.symbol);
  const id = `dlg-${++seq}`;
  const slIn = h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: pos.sl != null ? String(pos.sl) : '', placeholder: 'No stop-loss' });
  const tpIn = h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: pos.tp != null ? String(pos.tp) : '', placeholder: 'No take-profit' });
  const err = h('p', { class: 'dlg-error', role: 'alert' });
  const save = h('button', { type: 'button', class: 'btn primary' }, 'Save stops');
  const live = store.prices[pos.symbol];

  const dlg = h('dialog', { 'aria-labelledby': id },
    h('div', { class: 'dlg-head' },
      h('h2', { id }, `Stops for ${pos.side === 'long' ? 'buy' : 'sell'} ${pos.label || m.name}`)),
    h('div', { class: 'dlg-body' },
      h('p', { class: 'muted' }, `Entry ${price(m, pos.entry)}`, live && live.f ? ` · price now ${price(m, live.p)}` : '', pos.liq != null ? ` · auto-exit near ${price(m, pos.liq)}` : ''),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Stop-loss'), slIn,
        h('span', { class: 'field-hint' }, 'Moving the stop in your favour locks in profit. Your original risk (1R) stays the same in the journal.')),
      h('button', { type: 'button', class: 'btn sm', style: { justifySelf: 'start' }, on: { click: () => { slIn.value = String(pos.entry); } } }, 'Move stop to entry (break-even)'),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Take-profit'), tpIn,
        h('span', { class: 'field-hint' }, 'Leave a box empty to remove that stop.')),
      err),
    h('div', { class: 'dlg-foot' },
      h('button', { type: 'button', class: 'btn', on: { click: () => dlg.close() } }, 'Cancel'),
      save));

  save.addEventListener('click', async () => {
    const sl = parseOptional(slIn.value), tp = parseOptional(tpIn.value);
    if (sl.error || tp.error) { err.textContent = 'Enter a price above zero, or leave the box empty.'; return; }
    save.disabled = true; err.textContent = '';
    try {
      const r = await api(`/api/trades/${pos.id}/stops`, { method: 'PATCH', body: { sl: sl.value, tp: tp.value } });
      setAccount(r.account);
      dlg.close();
    } catch (e) {
      err.textContent = e.message;
      save.disabled = false;
    }
  });
  slIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') save.click(); });
  tpIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') save.click(); });
  openDialog(dlg);
  slIn.focus();
}

// ---------------------------------------------------------------- journal entry for one trade
export function openJournalDialog(trade, onSaved) {
  const m = market(trade.symbol);
  const id = `dlg-${++seq}`;
  const state = { rating: trade.rating || null };

  const setup = h('select', null, h('option', { value: '' }, 'No setup'),
    SETUPS.map((s) => h('option', { value: s, selected: trade.setup === s }, s)));
  const emotion = h('select', null, h('option', { value: '' }, 'Not sure'),
    EMOTIONS.map((s) => h('option', { value: s, selected: trade.emotion === s }, s)));
  const tags = h('input', { type: 'text', maxlength: '200', value: (trade.tags || []).join(', '), placeholder: 'e.g. news, expiry-day' });
  const note = h('textarea', { maxlength: '2000', placeholder: 'Why did you take this trade?' }, trade.note || '');
  const lesson = h('textarea', { maxlength: '2000', placeholder: 'What would you do differently?' }, trade.lesson || '');
  const err = h('p', { class: 'dlg-error', role: 'alert' });
  const save = h('button', { type: 'button', class: 'btn primary' }, 'Save journal');

  const starWrap = h('div', { class: 'chips', role: 'group', 'aria-label': 'Plan rating' });
  function drawStars() {
    starWrap.replaceChildren(
      ...[1, 2, 3, 4, 5].map((n) => h('button', {
        type: 'button', class: 'chip', 'aria-pressed': String(state.rating === n),
        'aria-label': `${n} out of 5`, style: state.rating === n ? { background: 'var(--accent-bg)', color: 'var(--accent)', borderColor: 'var(--accent)' } : null,
        on: { click: () => { state.rating = state.rating === n ? null : n; drawStars(); } },
      }, String(n))));
  }
  drawStars();

  const summary = [];
  if (trade.status === 'closed') {
    summary.push(
      h('p', null,
        h('strong', null, `${trade.side === 'long' ? 'Buy' : 'Sell'} ${trade.label || m.name}`), ': ',
        `${price(m, trade.entry)} → ${price(m, trade.exit)}, `,
        h('strong', { class: tone(trade.pnl) }, `${arrow(trade.pnl)} ${money(trade.pnl, { sign: true })}`),
        trade.r != null ? ` (${rMult(trade.r)})` : ''),
      h('p', { class: 'fine' }, `While it was open, price moved ${pct(trade.mfe_pct, { decimals: 2 })} your way and ${pct(trade.mae_pct, { decimals: 2 })} against you.`));
  }

  const dlg = h('dialog', { 'aria-labelledby': id },
    h('div', { class: 'dlg-head' }, h('h2', { id }, 'Trade journal')),
    h('div', { class: 'dlg-body' },
      ...summary,
      h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' } },
        h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Setup'), setup),
        h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'How did you feel going in?'), emotion)),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'How closely did you follow your plan?'), starWrap,
        h('span', { class: 'field-hint' }, '1 = not at all, 5 = exactly. Tap again to clear.')),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Tags'), tags),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'Notes'), note),
      h('label', { class: 'field' }, h('span', { class: 'field-label' }, 'What did you learn?'), lesson),
      err),
    h('div', { class: 'dlg-foot' },
      h('button', { type: 'button', class: 'btn', on: { click: () => dlg.close() } }, 'Cancel'),
      save));

  save.addEventListener('click', async () => {
    save.disabled = true; err.textContent = '';
    const tagList = tags.value.split(',').map((t) => t.trim().slice(0, 24)).filter(Boolean).slice(0, 8);
    try {
      const r = await api(`/api/trades/${trade.id}/journal`, {
        method: 'PATCH',
        body: { setup: setup.value, emotion: emotion.value, rating: state.rating, tags: tagList, note: note.value, lesson: lesson.value },
      });
      dlg.close();
      if (onSaved) onSaved(r.trade);
    } catch (e) {
      err.textContent = e.message;
      save.disabled = false;
    }
  });
  openDialog(dlg);
}

// ---------------------------------------------------------------- option order ticket dialog
export async function openOptionOrderDialog({
  leg,
  label,
  underlying,
  expiry,
  strike,
  type,
  defaultSide = 'long',
  onOrderPlaced,
}) {
  let inst = market(leg.id);
  if (!inst || !inst.lot_size || inst.kind === 'equity') {
    try {
      inst = await ensureInstrument(leg.id);
    } catch (_) {}
  }

  const id = `dlg-${++seq}`;
  const lotSize = (inst && inst.lot_size) || (leg && leg.lot_size) || 25;
  const tickSize = (inst && inst.tick) || 0.05;
  const initialPrice = leg.ltp != null ? leg.ltp : (inst && inst.price != null ? inst.price : null);

  const ui = {
    side: defaultSide === 'short' ? 'short' : 'long',
    product: 'MIS',
    type: 'market',
    limitPrice: initialPrice != null ? (Math.round(initialPrice / tickSize) * tickSize).toFixed(2) : '',
    lots: 1,
    sl: '',
    tp: '',
  };

  const prods = (inst && inst.products && inst.products.length) ? inst.products : ['MIS', 'NRML'];
  const allowedProds = prods.includes('CNC') ? prods : ['MIS', 'NRML'];
  if (!allowedProds.includes(ui.product)) ui.product = allowedProds[0];

  let busy = false;

  const dlg = h('dialog', { 'aria-labelledby': id, class: 'option-order-dlg' });

  // Header elements
  const closeBtn = h('button', { type: 'button', class: 'dlg-close-btn', 'aria-label': 'Close dialog' }, '✕');
  closeBtn.addEventListener('click', () => dlg.close());

  const priceEl = h('div', { class: 'dlg-opt-price' }, initialPrice != null ? price(inst, initialPrice) : '–');
  const metaBadges = [];
  if (strike != null) metaBadges.push(h('span', { class: 'badge accent' }, `Strike ${num(strike, 0)}`));
  if (type) metaBadges.push(h('span', { class: `badge ${type === 'CE' ? 'up' : 'down'}` }, type === 'CE' ? 'CALL' : 'PUT'));
  if (expiry) metaBadges.push(h('span', { class: 'badge' }, expiry));
  metaBadges.push(h('span', { class: 'badge' }, `Lot: ${lotSize}`));

  const head = h('div', { class: 'dlg-opt-head' },
    h('div', { class: 'dlg-opt-title' },
      h('h2', { id }, label || inst.label || inst.name),
      h('div', { class: 'dlg-opt-meta' }, metaBadges)),
    h('div', { class: 'dlg-opt-price-box' },
      h('div', null, h('span', { class: 'fine' }, 'LTP '), priceEl),
      closeBtn));

  // Side Selector (Buy / Sell)
  const buyBtn = h('button', {
    type: 'button', class: 'seg-btn long', 'aria-pressed': String(ui.side === 'long'),
    on: { click: () => { ui.side = 'long'; render(); schedulePreview(); } },
  }, '▲ BUY');
  const sellBtn = h('button', {
    type: 'button', class: 'seg-btn short', 'aria-pressed': String(ui.side === 'short'),
    on: { click: () => { ui.side = 'short'; render(); schedulePreview(); } },
  }, '▼ SELL');
  const sideSeg = h('div', { class: 'seg side-seg', role: 'group', 'aria-label': 'Order side' }, buyBtn, sellBtn);

  // Product Selector
  const prodNote = h('p', { class: 'prod-note' });
  const prodBtns = allowedProds.map((p) => h('button', {
    type: 'button', class: 'seg-btn', 'aria-pressed': String(ui.product === p),
    on: { click: () => { ui.product = p; render(); schedulePreview(); } },
  }, p));
  const prodSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Product type' }, prodBtns);

  // Order Type Selector (Market / Limit)
  const mktBtn = h('button', {
    type: 'button', class: 'seg-btn', 'aria-pressed': String(ui.type === 'market'),
    on: { click: () => { ui.type = 'market'; render(); schedulePreview(); } },
  }, 'Market');
  const lmtBtn = h('button', {
    type: 'button', class: 'seg-btn', 'aria-pressed': String(ui.type === 'limit'),
    on: { click: () => {
      ui.type = 'limit';
      if (!ui.limitPrice && initialPrice != null) ui.limitPrice = (Math.round(initialPrice / tickSize) * tickSize).toFixed(2);
      render();
      schedulePreview();
    } },
  }, 'Limit');
  const typeSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Order type' }, mktBtn, lmtBtn);

  // Limit Price input & steppers
  const limitInput = h('input', {
    type: 'text', inputmode: 'decimal', placeholder: 'Limit price', value: ui.limitPrice, class: 'stepper-input'
  });
  limitInput.addEventListener('input', () => { ui.limitPrice = limitInput.value; schedulePreview(); });
  const limitDec = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
    const cur = parseFloat(ui.limitPrice) || initialPrice || 0;
    ui.limitPrice = Math.max(0.05, cur - tickSize).toFixed(2);
    limitInput.value = ui.limitPrice;
    schedulePreview();
  } } }, '−');
  const limitInc = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
    const cur = parseFloat(ui.limitPrice) || initialPrice || 0;
    ui.limitPrice = (cur + tickSize).toFixed(2);
    limitInput.value = ui.limitPrice;
    schedulePreview();
  } } }, '+');
  const limitField = h('div', { class: 'field' },
    h('span', { class: 'field-label' }, 'Limit Price (₹)'),
    h('div', { class: 'stepper' }, limitDec, limitInput, limitInc),
    h('span', { class: 'field-hint' }, ui.side === 'long' ? 'Must be below current LTP for limit buy' : 'Must be above current LTP for limit sell'));

  // Lots & Quantity Stepper
  const lotInput = h('input', {
    type: 'text', inputmode: 'numeric', value: String(ui.lots), class: 'stepper-input'
  });
  lotInput.addEventListener('input', () => {
    const n = parseInt(lotInput.value.replace(/\D/g, ''), 10);
    ui.lots = Number.isInteger(n) && n > 0 ? Math.min(n, 1000) : 1;
    renderLots();
    schedulePreview();
  });
  const lotDec = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
    if (ui.lots > 1) { ui.lots -= 1; lotInput.value = String(ui.lots); renderLots(); schedulePreview(); }
  } } }, '−');
  const lotInc = h('button', { type: 'button', class: 'stepper-btn', on: { click: () => {
    ui.lots += 1; lotInput.value = String(ui.lots); renderLots(); schedulePreview();
  } } }, '+');
  const lotChips = [1, 2, 5, 10].map((n) => h('button', {
    type: 'button', class: 'chip',
    on: { click: () => { ui.lots = n; lotInput.value = String(n); renderLots(); schedulePreview(); } },
  }, `${n} Lot${n > 1 ? 's' : ''}`));
  const lotSharesHint = h('span', { class: 'field-hint', style: { fontWeight: '600' } });

  function renderLots() {
    lotSharesHint.textContent = `Total: ${num(ui.lots * lotSize, 0)} shares (${ui.lots} lot${ui.lots > 1 ? 's' : ''} × ${lotSize})`;
  }
  renderLots();

  const lotsField = h('div', { class: 'field' },
    h('div', { class: 'field-row' },
      h('span', { class: 'field-label' }, 'Quantity (Lots)'),
      h('div', { class: 'chips' }, lotChips)),
    h('div', { class: 'stepper' }, lotDec, lotInput, lotInc),
    lotSharesHint);

  // Stop-loss & Take-profit (Collapsible)
  const slIn = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'Stop-loss price (optional)', value: ui.sl });
  slIn.addEventListener('input', () => { ui.sl = slIn.value; schedulePreview(); });
  const slChips = [10, 20, 30].map((p) => h('button', {
    type: 'button', class: 'chip',
    on: { click: () => {
      const ref = (ui.type === 'limit' && parseFloat(ui.limitPrice)) || initialPrice;
      if (!ref) return;
      const v = ui.side === 'long' ? ref * (1 - p / 100) : ref * (1 + p / 100);
      ui.sl = (Math.round(v / tickSize) * tickSize).toFixed(2);
      slIn.value = ui.sl;
      schedulePreview();
    } },
  }, `−${p}%`));

  const tpIn = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'Target price (optional)', value: ui.tp });
  tpIn.addEventListener('input', () => { ui.tp = tpIn.value; schedulePreview(); });
  const tpChips = [20, 50, 100].map((p) => h('button', {
    type: 'button', class: 'chip',
    on: { click: () => {
      const ref = (ui.type === 'limit' && parseFloat(ui.limitPrice)) || initialPrice;
      if (!ref) return;
      const v = ui.side === 'long' ? ref * (1 + p / 100) : ref * (1 - p / 100);
      ui.tp = (Math.round(v / tickSize) * tickSize).toFixed(2);
      tpIn.value = ui.tp;
      schedulePreview();
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

  // Preview Box
  const previewBox = h('div', { class: 'opt-preview-card' });

  // Error & Submit
  const errEl = h('p', { class: 'dlg-error', role: 'alert' });
  const submitBtn = h('button', { type: 'button', class: `btn submit ${ui.side}`, on: { click: submitOrder } });
  const cancelBtn = h('button', { type: 'button', class: 'btn', on: { click: () => dlg.close() } }, 'Cancel');

  function render() {
    buyBtn.setAttribute('aria-pressed', String(ui.side === 'long'));
    sellBtn.setAttribute('aria-pressed', String(ui.side === 'short'));

    prodBtns.forEach((b, idx) => {
      b.setAttribute('aria-pressed', String(allowedProds[idx] === ui.product));
    });
    if (ui.product === 'MIS') prodNote.textContent = 'MIS: Intraday with ~5x leverage. Auto squared-off at 3:20 PM.';
    else if (ui.product === 'NRML') prodNote.textContent = 'NRML: Carry forward overnight until expiry date.';
    else prodNote.textContent = 'CNC: Delivery product.';

    mktBtn.setAttribute('aria-pressed', String(ui.type === 'market'));
    lmtBtn.setAttribute('aria-pressed', String(ui.type === 'limit'));
    limitField.style.display = ui.type === 'limit' ? 'grid' : 'none';

    renderSubmit();
  }

  function renderSubmit() {
    const verb = ui.type === 'limit' ? (ui.side === 'long' ? 'BUY LIMIT' : 'SELL LIMIT') : (ui.side === 'long' ? 'BUY' : 'SELL');
    submitBtn.className = `btn submit ${ui.side}`;
    submitBtn.textContent = busy ? 'Placing order…' : `${verb} ${ui.lots} LOT${ui.lots > 1 ? 'S' : ''} · ${label || inst.name}`;
  }

  function renderPreview(p, err) {
    if (err) {
      mount(previewBox, h('span', { class: 'opt-preview-warn' }, err));
      return;
    }
    if (!p) {
      mount(previewBox, h('span', { class: 'fine' }, 'Enter quantity to preview margin and charges.'));
      return;
    }
    mount(previewBox,
      h('div', { class: 'opt-preview-row' },
        h('span', null, 'Margin needed'),
        h('strong', null, `${money(p.margin)} (Free: ${money(Math.max(p.available, 0))})`)),
      h('div', { class: 'opt-preview-row' },
        h('span', null, 'Contract value'),
        h('strong', null, money(p.value))),
      h('div', { class: 'opt-preview-row' },
        h('span', null, 'Charges to open'),
        h('strong', null, money(p.fees_open, { decimals: 2 }))),
      p.warnings && p.warnings.length
        ? h('div', { class: 'opt-preview-warn' }, p.warnings.join(' · '))
        : null);
  }

  const schedulePreview = debounce(fetchPreview, 200);

  async function fetchPreview() {
    const q = {
      symbol: inst.id,
      side: ui.side,
      product: ui.product,
      lots: ui.lots,
    };
    if (ui.type === 'limit') {
      const lp = parseOptional(ui.limitPrice);
      if (!lp.value) { renderPreview(null); return; }
      q.entry = lp.value;
    }
    const sl = parseOptional(ui.sl).value;
    const tp = parseOptional(ui.tp).value;
    if (sl) q.stop = sl;
    if (tp) q.tp = tp;

    try {
      const r = await api('/api/risk/size', { params: q });
      renderPreview(r);
    } catch (e) {
      renderPreview(null, e.message);
    }
  }

  async function submitOrder() {
    errEl.textContent = '';
    busy = true;
    renderSubmit();
    submitBtn.disabled = true;

    const body = {
      symbol: inst.id,
      side: ui.side,
      product: ui.product,
      type: ui.type,
      lots: ui.lots,
    };
    if (ui.type === 'limit') {
      const lp = parseOptional(ui.limitPrice);
      if (lp.error || !lp.value) {
        errEl.textContent = 'Enter a valid limit price.';
        busy = false;
        submitBtn.disabled = false;
        renderSubmit();
        return;
      }
      body.limit_price = lp.value;
    }
    const sl = parseOptional(ui.sl).value;
    if (sl) body.sl = sl;
    const tp = parseOptional(ui.tp).value;
    if (tp) body.tp = tp;

    try {
      const r = await api('/api/orders', { method: 'POST', body });
      setAccount(r.account);
      const actionText = r.result.kind === 'order' ? 'Limit order placed for' : (ui.side === 'long' ? 'Bought' : 'Sold');
      toast(`${actionText} ${ui.lots} lot${ui.lots > 1 ? 's' : ''} of ${label || inst.name}!`, 'ok');
      dlg.close();
      if (onOrderPlaced) onOrderPlaced(r);
    } catch (e) {
      errEl.textContent = e.message;
    } finally {
      busy = false;
      submitBtn.disabled = false;
      renderSubmit();
    }
  }

  // Assemble dialog body
  const body = h('div', { class: 'dlg-body' },
    sideSeg,
    h('div', { class: 'field' },
      h('span', { class: 'field-label' }, 'Product'),
      prodSeg,
      prodNote),
    h('div', { class: 'field' },
      h('span', { class: 'field-label' }, 'Order Type'),
      typeSeg),
    limitField,
    lotsField,
    targetsWrap,
    previewBox,
    errEl);

  const foot = h('div', { class: 'dlg-foot', style: { display: 'flex', gap: '10px' } },
    cancelBtn,
    submitBtn);

  mount(dlg, head, body, foot);

  render();
  openDialog(dlg);
  schedulePreview();
}
