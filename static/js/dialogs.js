// Modal dialogs shared by several views. They use the native <dialog> element, so Escape,
// focus trapping and the backdrop come from the browser.
import { api } from './api.js';
import { h, num, money, pct, rMult, price, qtyText, tone, arrow } from './util.js';
import { store, market, setAccount } from './store.js';

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
