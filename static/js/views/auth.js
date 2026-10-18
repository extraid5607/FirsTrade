// Signed-out screen: what the app is, a worked risk example, live prices, and the login form.
import { api } from '../api.js';
import { h, mount, price, pct, tone, arrow } from '../util.js';
import { store, on } from '../store.js';

const STRIP = ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'HDFCBANK', 'INFY'];

// A static copy of the order-panel risk ladder, so a new visitor sees what "R" means.
function exampleLadder() {
  const entry = 1500, stop = 1485, target = 1527, liq = 1440;   // buy, intraday
  const H = 190, PAD = 16, hi = target, lo = liq, span = hi - lo;
  const y = (v) => PAD + ((hi - v) / span) * (H - 2 * PAD);
  const items = [
    { key: 'tp', name: 'Target', p: target, r: '+1.8R' },
    { key: 'entry', name: 'Entry', p: entry, r: '' },
    { key: 'sl', name: 'Stop', p: stop, r: '−1.0R' },
    { key: 'liq', name: 'Auto-exit', p: liq, r: '−4.0R' },
  ].map((i) => ({ ...i, y: y(i.p), ly: y(i.p) }));
  for (let i = 1; i < items.length; i++) if (items[i].ly < items[i - 1].ly + 34) items[i].ly = items[i - 1].ly + 34;
  const zone = (a, b, cls) => h('div', { class: `zone ${cls}`, style: { top: `${Math.min(a, b)}px`, height: `${Math.abs(a - b)}px` } });
  const by = Object.fromEntries(items.map((i) => [i.key, i]));
  return h('div', { class: 'ladder', style: { height: `${H}px` }, role: 'img',
    'aria-label': 'Example: target at plus 1.8 R, entry, stop at minus 1 R, and the risk-system exit at minus 4 R' },
  h('div', { class: 'rail' }),
  zone(by.entry.y, by.sl.y, 'zone-risk'), zone(by.entry.y, by.tp.y, 'zone-reward'), zone(by.sl.y, by.liq.y, 'zone-liq'),
  items.map((i) => h('span', { class: `tick tick-${i.key}`, style: { top: `${i.y}px` } })),
  items.map((i) => h('div', { class: `rung rung-${i.key}`, style: { top: `${i.ly}px` } },
    h('span', { class: 'rung-name' }, i.name),
    h('span', { class: 'rung-price' }, i.p.toLocaleString('en-IN')),
    h('span', { class: 'rung-r' }, i.r))));
}

export function renderAuth(root, onAuthed) {
  let mode = 'login';
  const unsub = [];

  const modeSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Log in or create an account' });
  const username = h('input', { type: 'text', id: 'a-user', autocomplete: 'username', maxlength: '20', spellcheck: 'false', autocapitalize: 'none' });
  const password = h('input', { type: 'password', id: 'a-pass', maxlength: '200' });
  const invite = h('input', { type: 'text', id: 'a-invite', maxlength: '64', autocomplete: 'off', spellcheck: 'false', autocapitalize: 'none' });
  const inviteField = h('div', { class: 'field' }, h('label', { class: 'field-label', for: 'a-invite' }, 'Invite code'), invite,
    h('span', { class: 'field-hint' }, 'Ask your teacher for the class invite code.'));
  const err = h('p', { class: 'auth-error', role: 'alert' });
  const submit = h('button', { type: 'submit', class: 'btn primary', style: { minHeight: '42px' } });
  const hintUser = h('span', { class: 'field-hint' });
  const hintPass = h('span', { class: 'field-hint' });
  const form = h('form', { novalidate: true },
    h('div', { class: 'field' }, h('label', { class: 'field-label', for: 'a-user' }, 'Username'), username, hintUser),
    h('div', { class: 'field' }, h('label', { class: 'field-label', for: 'a-pass' }, 'Password'), password, hintPass),
    inviteField, err, submit);

  function drawMode() {
    mount(modeSeg, [['login', 'Log in'], ['register', 'Create account']].map(([v, t]) => h('button', {
      type: 'button', class: 'seg-btn', 'aria-pressed': String(v === mode),
      on: { click: () => { mode = v; err.textContent = ''; drawMode(); } },
    }, t)));
    submit.textContent = mode === 'login' ? 'Log in' : 'Create account and start with ₹10,00,000';
    password.setAttribute('autocomplete', mode === 'login' ? 'current-password' : 'new-password');
    inviteField.hidden = !(mode === 'register' && store.me && store.me.invite_required);
    hintUser.textContent = mode === 'register' ? '3 to 20 letters, numbers or underscores. This is your name on the leaderboard.' : '';
    hintPass.textContent = mode === 'register' ? 'At least 8 characters. There is no email, so there is no password reset: keep it safe.' : '';
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const u = username.value.trim(), p = password.value;
    if (!u || !p) { err.textContent = 'Enter your username and password.'; return; }
    submit.disabled = true; err.textContent = '';
    try {
      const r = await api(mode === 'login' ? '/api/auth/login' : '/api/auth/register', { method: 'POST', body: mode === 'login' ? { username: u, password: p } : { username: u, password: p, invite: invite.value.trim() } });
      store.me.user = r.user;
      onAuthed();
    } catch (ex) {
      err.textContent = ex.message;
      submit.disabled = false;
    }
  });

  // live price strip
  const strip = h('div', { class: 'strip', 'aria-label': 'Live prices' });
  const cells = {};
  for (const id of STRIP) {
    const m = store.byId[id];
    if (!m) continue;
    cells[id] = { p: h('strong'), c: h('span') };
    strip.append(h('div', { class: 'strip-item' }, h('span', null, m.name), cells[id].p, cells[id].c));
  }
  function paintStrip() {
    for (const [id, c] of Object.entries(cells)) {
      const m = store.byId[id], q = store.prices[id];
      c.p.textContent = q ? price(m, q.p) : '–';
      if (q && !q.f) { c.c.textContent = 'Last close'; c.c.className = ''; }
      else if (q && q.c != null) { c.c.textContent = `${arrow(q.c)} ${pct(q.c, { sign: true })}`.trim(); c.c.className = tone(q.c); }
      else c.c.textContent = '';
    }
  }
  unsub.push(on('prices', paintStrip));

  mount(root,
    h('div', { class: 'auth' },
      h('div', { class: 'auth-brand' },
        h('span', { class: 'brand' },
          h('img', { src: '/logo.svg', alt: 'FirsTrade logo', class: 'brand-logo' }),
          'FirsTrade')),
      h('div', null,
        h('h1', null, 'Learn Indian markets with pretend money'),
        h('p', { class: 'auth-lead' }, 'Start with ₹10,00,000. Trade Nifty, Bank Nifty and stocks, plus futures and options with a live option chain, and find out what a trade would really have cost you before it costs you anything.'),
        h('ul', { class: 'auth-points' },
          h('li', null, h('strong', null, 'See your risk before you click. '), 'Set a stop-loss and the order panel shows your lots, margin, charges and reward, all measured in R.'),
          h('li', null, h('strong', null, 'Keep a journal. '), 'Note why you took each trade and how you felt, then see which setups and moods make or lose money.'),
          h('li', null, h('strong', null, 'Set your own rules. '), 'Require stop-losses, cap your risk per trade and your daily loss. The simulator holds you to them.'),
          h('li', null, h('strong', null, 'Compare with others. '), 'A leaderboard ranks practice accounts by return.')),
        h('section', { class: 'panel example' },
          h('h2', null, 'Example: buy with a 1% stop, intraday (MIS)'),
          exampleLadder(),
          h('p', { class: 'fine' }, 'Example numbers. 1R is what you lose if the stop is hit, charges included. The risk system closes a leveraged position automatically long before your money is gone, so the stop does the real work.'))),
      h('section', { class: 'panel auth-card', 'aria-label': 'Log in or create an account' }, modeSeg, form)),
    strip,
    h('p', { class: 'disclaimer' }, 'FirsTrade uses pretend money and is for learning only. It is not investment advice, there are no prizes or real trades, and results here do not predict real trading results. Prices may be delayed or simulated.'));

  drawMode();
  paintStrip();
  return function destroy() { unsub.forEach((f) => f()); };
}
