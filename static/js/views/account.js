import { api } from '../api.js';
import { h, mount, money, tone, toast } from '../util.js';
import { store, on, setAccount, stayQuiet } from '../store.js';
import { confirmDialog } from '../dialogs.js';

export function renderAccount(root) {
  const unsub = [];

  function draw() {
    const u = store.me?.user;
    const a = store.account;
    if (!u) {
      mount(root, h('div', { class: 'page page-narrow' }, h('p', { class: 'empty' }, 'Please log in to view account details.')));
      return;
    }

    const equityCard = h('div', { class: 'panel acct-panel' },
      h('div', { class: 'acct-header' },
        h('div', { class: 'acct-avatar' }, u.username.slice(0, 2).toUpperCase()),
        h('div', null,
          h('h2', { class: 'acct-username' }, u.username),
          h('p', { class: 'fine' }, `Season ${u.season} · Student Paper Account`))),
      h('div', { class: 'acct-metrics' },
        h('div', { class: 'acct-metric primary' },
          h('span', null, 'Total Equity'),
          h('strong', null, a ? money(a.equity) : '–')),
        h('div', { class: 'acct-metric' },
          h('span', null, "Today's P&L"),
          h('strong', { class: a ? tone(a.day_pnl) : '' }, a ? money(a.day_pnl, { sign: true }) : '–')),
        h('div', { class: 'acct-metric' },
          h('span', null, 'Free Cash / Margin'),
          h('strong', null, a ? money(Math.max(a.available, 0)) : '–')),
        h('div', { class: 'acct-metric' },
          h('span', null, 'Margin In Use'),
          h('strong', null, a ? money(a.margin_used) : '–')),
        h('div', { class: 'acct-metric' },
          h('span', null, 'Open P&L'),
          h('strong', { class: a ? tone(a.unrealized) : '' }, a ? money(a.unrealized, { sign: true }) : '–'))));

    const rulesCard = h('div', { class: 'panel acct-rules-card' },
      h('h3', null, 'Risk Rules & Margins'),
      h('div', { class: 'acct-rule-row' },
        h('span', null, 'Max risk per trade'),
        h('strong', null, `${u.max_risk_pct}%`)),
      h('div', { class: 'acct-rule-row' },
        h('span', null, 'Daily loss limit'),
        h('strong', null, `${u.daily_loss_pct}%`)),
      h('div', { class: 'acct-rule-row' },
        h('span', null, 'Stop-loss on every order'),
        h('strong', null, u.require_sl ? 'Required' : 'Optional')),
      h('div', { class: 'acct-rule-row' },
        h('span', null, 'Intraday (MIS) auto-squareoff'),
        h('strong', null, '15:20 IST')));

    const resetBtn = h('button', {
      type: 'button',
      class: 'btn danger solid',
      on: {
        click: () => {
          confirmDialog({
            title: `Start Season ${u.season + 1}?`,
            body: `This closes any open trades, clears orders and restores cash to ${money(u.start_balance, { decimals: 0 })}. Your past trades stay saved in history.`,
            confirmText: 'Reset account',
            danger: true,
            action: async () => {
              stayQuiet();
              const r = await api('/api/account/reset', { method: 'POST', body: { confirm: true } });
              store.me.user = r.user;
              setAccount(r.account);
              toast(`Season ${r.user.season} started with ${money(r.user.start_balance, { decimals: 0 })}.`, 'ok');
              draw();
            },
          });
        },
      },
    }, `Reset Account (Fresh Season ${u.season + 1})`);

    const actionsCard = h('div', { class: 'panel acct-actions-card' },
      h('h3', null, 'Account Actions'),
      h('p', { class: 'fine', style: { marginBottom: '12px' } }, 'Resetting your balance starts a fresh season. Past journal notes stay intact.'),
      resetBtn);

    mount(root, h('div', { class: 'page page-narrow' },
      h('div', { class: 'page-head' },
        h('h1', null, 'Account & Profile'),
        h('p', { class: 'page-sub' }, 'Manage your virtual trading funds, risk parameters, and seasons.')),
      equityCard,
      rulesCard,
      actionsCard));
  }

  unsub.push(on('account', draw));
  draw();

  return function destroy() {
    unsub.forEach((f) => f());
  };
}
