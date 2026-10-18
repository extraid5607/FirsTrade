// Rules: the limits you set for yourself, how the simulation works, and account reset.
import { api } from '../api.js';
import { h, mount, money, toast } from '../util.js';
import { store, setAccount, emit, stayQuiet } from '../store.js';
import { confirmDialog } from '../dialogs.js';

const BEGINNER = { require_sl: true, max_risk_pct: 1, daily_loss_pct: 3 };

export function renderRules(root) {
  const user = store.me.user;
  const s = user.settings;

  const requireSl = h('input', { type: 'checkbox', id: 'r-sl', checked: s.require_sl });
  const maxRisk = h('input', { type: 'number', id: 'r-risk', min: '0.1', max: '100', step: '0.1', value: String(s.max_risk_pct), inputmode: 'decimal' });
  const dayLoss = h('input', { type: 'number', id: 'r-day', min: '0', max: '100', step: '0.5', value: String(s.daily_loss_pct), inputmode: 'decimal' });
  const err = h('p', { class: 'dlg-error', role: 'alert' });
  const saveBtn = h('button', { type: 'button', class: 'btn primary' }, 'Save rules');

  function fill(v) {
    requireSl.checked = v.require_sl;
    maxRisk.value = String(v.max_risk_pct);
    dayLoss.value = String(v.daily_loss_pct);
  }

  async function save() {
    const risk = Number(maxRisk.value), day = Number(dayLoss.value);
    if (!(risk >= 0.1 && risk <= 100)) { err.textContent = 'Risk per trade must be between 0.1% and 100%.'; return; }
    if (!(day >= 0 && day <= 100)) { err.textContent = 'Daily loss limit must be between 0% and 100%. Use 0 to turn it off.'; return; }
    err.textContent = '';
    saveBtn.disabled = true;
    try {
      const r = await api('/api/settings', { method: 'PATCH', body: { require_sl: requireSl.checked, max_risk_pct: risk, daily_loss_pct: day } });
      store.me.user = r.user;
      toast('Rules saved.', 'ok');
    } catch (e) {
      err.textContent = e.message;
    } finally {
      saveBtn.disabled = false;
    }
  }
  saveBtn.addEventListener('click', save);

  async function resetAccount() {
    const ok = await confirmDialog({
      title: 'Reset your account?',
      body: `This closes every open position at the current price, cancels pending orders, and starts season ${user.season + 1} with ${money(user.start_balance, { decimals: 0 })}. Your journal, stats and leaderboard rank only count the new season.`,
      confirmLabel: 'Reset account', danger: true,
    });
    if (!ok) return;
    try {
      stayQuiet();
      const r = await api('/api/account/reset', { method: 'POST', body: { confirm: true } });
      store.me.user = r.user;
      setAccount(r.account);
      emit('closed', []);
      toast(`Season ${r.user.season} started with ${money(r.user.start_balance, { decimals: 0 })}.`, 'ok');
      renderRules(root);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  mount(root, h('div', { class: 'page' },
    h('div', { class: 'page-head' }, h('h1', null, 'Rules')),
    h('p', { class: 'page-sub' }, 'Set limits for yourself. The simulator enforces them on every order, the way a disciplined trader would.'),
    h('div', { class: 'rules-grid' },
      h('div', { style: { display: 'grid', gap: '14px' } },
        h('section', { class: 'panel panel-pad form-grid' },
          h('h2', null, 'Your risk limits'),
          h('div', { class: 'check-row' }, requireSl, h('label', { for: 'r-sl' }, 'Require a stop-loss on every trade', h('small', null, 'Orders without a stop-loss are refused.'))),
          h('div', { class: 'field' }, h('label', { class: 'field-label', for: 'r-risk' }, 'Maximum risk per trade (% of equity)'), maxRisk,
            h('span', { class: 'field-hint' }, 'Counted from your entry to your stop-loss, fees included.')),
          h('div', { class: 'field' }, h('label', { class: 'field-label', for: 'r-day' }, 'Daily loss limit (% of equity at the start of the day)'), dayLoss,
            h('span', { class: 'field-hint' }, 'After this loss you cannot open new trades until the next trading day. Use 0 to turn it off.')),
          err,
          h('div', { class: 'rules-actions' }, saveBtn,
            h('button', { type: 'button', class: 'btn', on: { click: () => fill(BEGINNER) } }, 'Fill in beginner rules'))),
        h('section', { class: 'panel panel-pad form-grid danger-zone' },
          h('h2', null, 'Account'),
          h('p', null, `Season ${user.season}. You started with ${money(user.start_balance, { decimals: 0 })}.`),
          h('p', { class: 'fine' }, 'Blown the account, or want a clean start? Resetting keeps your username.'),
          h('div', { class: 'rules-actions' }, h('button', { type: 'button', class: 'btn danger', on: { click: resetAccount } }, 'Reset account')))),
      h('section', { class: 'panel panel-pad form-grid' },
        h('h2', null, 'How the simulation works'),
        h('ul', { class: 'explain' },
          h('li', null, h('strong', null, 'Hours. '), 'The NSE is open Monday to Friday, 9:15 am to 3:30 pm India time, except exchange holidays. Orders are only accepted while it is open. Limit and day orders lapse at the close.'),
          h('li', null, h('strong', null, 'CNC (delivery). '), 'You pay the full value and can only buy shares. Nothing is closed for you at the end of the day.'),
          h('li', null, h('strong', null, 'MIS (intraday). '), 'Roughly 5× leverage on shares and F&O. Anything still open at 3:20 pm is closed automatically at the market price.'),
          h('li', null, h('strong', null, 'NRML (F&O carry). '), 'Futures and options held overnight. Margin is approximate: about 11% of the contract for futures and 12% of the underlying for short options. A bought option costs its premium and nothing more.'),
          h('li', null, h('strong', null, 'Lots. '), 'Futures and options trade in lots (for example NIFTY is 65 per lot). Enter lots, not units.'),
          h('li', null, h('strong', null, 'Expiry. '), 'Expiry is on Tuesdays (or the day before if that is a holiday). Contracts settle in cash: futures at the index or share price, options at their intrinsic value.'),
          h('li', null, h('strong', null, 'Risk-system exit. '), 'If a leveraged position loses about 90% of its margin it is closed automatically, the way a broker\'s risk system would.'),
          h('li', null, h('strong', null, 'Fills. '), 'Market orders fill at the latest price. A stop-loss fills at the price when it triggers, so in a fast move the loss can be bigger than 1R.'),
          h('li', null, h('strong', null, 'Charges. '), 'Brokerage, STT, exchange and SEBI fees, stamp duty and GST are deducted on every order. The rates are close to typical discount-broker rates but are approximations.'),
          h('li', null, h('strong', null, 'Prices. '), 'Prices can lag by a few seconds, and the screen shows Market closed outside trading hours. Demo mode shows simulated prices.')),
        h('p', { class: 'fine' }, 'FirsTrade is for learning with pretend money. Real trading has spreads, slippage and delays that are not modelled here, so results will differ. Nothing here is investment advice, and the creator is not a SEBI-registered adviser.')))));
}
