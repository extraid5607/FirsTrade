// App shell: top bar, page switching (URL hash), login state, theme toggle.
import { api, setUnauthorizedHandler } from './api.js';
import { h, mount, money, tone, toast, storage } from './util.js';
import {
  store, on, loadMe, loadMarkets, connectPrices, refreshAccount, startAccountPolling,
  stopAccountPolling, setAccount, startMarketPolling,
} from './store.js';
import { renderAuth } from './views/auth.js';
import { renderWatchlist } from './views/watchlist.js';
import { renderTrade } from './views/trade.js';
import { renderPositions } from './views/positions.js';
import { renderTools } from './views/tools.js';
import { renderAccount } from './views/account.js';

const ROUTES = [
  ['watchlist', 'Watchlist', renderWatchlist],
  ['trade', 'Trade', renderTrade],
  ['positions', 'Positions', renderPositions],
  ['tools', 'Tools', renderTools],
  ['account', 'Account', renderAccount],
];
const ROUTE_TITLES = {
  watchlist: 'Watchlist',
  trade: 'Trade',
  positions: 'Positions & Orders',
  tools: 'Tools & Analysis',
  account: 'Account & Profile',
  chain: 'Option chain',
  journal: 'Journal',
  stats: 'Stats',
  rules: 'Risk rules',
  leaderboard: 'Leaderboard',
};

const root = document.getElementById('app');
let shell = null;        // { main, nav, acct, banner } while logged in
let destroyView = null;  // cleanup function of the page that is on screen
let unsubShell = [];

function routeFromHash() {
  const hash = (location.hash || '').replace(/^#\/?/, '').split(/[?/]/)[0];
  if (['chain', 'journal', 'stats', 'rules', 'leaderboard'].includes(hash)) {
    return { main: 'tools', sub: hash };
  }
  const valid = ROUTES.some((r) => r[0] === hash);
  return { main: valid ? hash : 'trade', sub: null };
}

function stopView() {
  if (destroyView) { try { destroyView(); } catch (e) { console.error(e); } }
  destroyView = null;
}

function setTitle(label) {
  const name = (store.me && store.me.app_name) || 'FirsTrade';
  document.title = label ? `${label} · ${name}` : `${name}: practice trading with pretend money`;
}

// ---------------------------------------------------------------- theme
function effectiveTheme() {
  const set = document.documentElement.getAttribute('data-theme');
  if (set === 'light' || set === 'dark') return set;
  return window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function toggleTheme() {
  const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  storage('zp_theme', next);
  drawThemeButton();
  if (shell) showRoute(false);   // charts read their colours when created, so redraw the page
}

let themeBtn = null;
function drawThemeButton() {
  if (!themeBtn) return;
  const dark = effectiveTheme() === 'dark';
  themeBtn.textContent = dark ? 'Light' : 'Dark';
  themeBtn.setAttribute('aria-label', dark ? 'Switch to the light theme' : 'Switch to the dark theme');
}

// ---------------------------------------------------------------- logged-in shell
function acctItem(label, value, cls = '', extra = '') {
  return h('div', { class: `acct-item ${extra}`.trim() }, h('span', null, label), h('strong', { class: cls }, value));
}

function drawAccount() {
  if (!shell) return;
  const a = store.account;
  if (!a) { mount(shell.acct, acctItem('Equity', '–', '', 'big')); return; }
  mount(shell.acct,
    h('div', { class: 'acct-item big' },
      h('span', null, 'Equity'),
      h('strong', null, money(a.equity), a.locked ? h('span', { class: 'badge warn lock-badge', title: 'Daily loss limit reached. New trades unlock on the next trading day.' }, 'Locked') : null)),
    acctItem('Today', money(a.day_pnl, { sign: true }), tone(a.day_pnl), 'hide-xs'),
    acctItem('Available', money(Math.max(a.available, 0)), '', 'hide-md hide-sm'),
    acctItem('Open P&L', money(a.unrealized, { sign: true }), tone(a.unrealized), 'hide-md hide-sm'));
}

function drawMarket() {
  if (!shell || !shell.mkt) return;
  const m = store.market;
  if (!m) { shell.mkt.textContent = ''; return; }
  shell.mkt.className = `badge mkt-badge ${m.open ? 'up' : ''}`;
  mount(shell.mkt, h('span', { 'aria-hidden': 'true' }, '● '), h('span', { class: 'mkt-text' }, m.forced ? 'Market open (demo)' : m.open ? 'Market open' : 'Market closed'));
  shell.mkt.title = m.label || (m.open ? 'NSE is open' : 'NSE is closed');
}

function drawNav(active) {
  mount(shell.nav, ROUTES.map(([id, label]) => h('a', {
    href: `#/${id}`, 'aria-current': id === active ? 'page' : null,
  }, label)));
}

async function logout() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* leaving anyway */ }
  endSession();
}

function endSession(message) {
  stopAccountPolling();
  stopView();
  unsubShell.forEach((f) => f());
  unsubShell = [];
  shell = null;
  store.account = null;
  if (store.me) store.me.user = null;
  location.hash = '';
  renderLoggedOut();
  if (message) toast(message, 'info');
}

function renderLoggedIn() {
  stopView();
  const main = h('main', { id: 'main', tabindex: '-1' });
  const nav = h('nav', { class: 'nav', 'aria-label': 'Pages' });
  const acct = h('div', { class: 'acct', 'aria-live': 'off' });
  themeBtn = h('button', { type: 'button', class: 'btn ghost sm', on: { click: toggleTheme } });
  const logoutBtn = h('button', { type: 'button', class: 'btn ghost sm', on: { click: logout } }, 'Log out');
  const banner = h('div', { class: 'banner', role: 'note' });
  const mkt = h('span', { class: 'badge mkt-badge', role: 'status' });
  const top = h('header', { class: 'topbar' },
    h('a', { class: 'brand', href: '#/trade' },
      h('img', { src: '/logo.svg', alt: 'FirsTrade logo', class: 'brand-logo' }),
      store.me.app_name),
    nav, acct,
    h('div', { class: 'topbar-tools' },
      mkt,
      h('span', { class: 'badge accent hide-sm', title: 'Signed in as' }, store.me.user.username),
      themeBtn, logoutBtn));
  mount(root, top, store.me.demo ? banner : null, main);
  if (store.me.demo) banner.textContent = 'Demo mode: prices are simulated and move faster than real markets. Nothing here is a real quote.';
  shell = { main, nav, acct, banner, mkt };
  drawThemeButton();
  drawAccount();
  drawMarket();
  unsubShell.push(on('account', drawAccount));
  unsubShell.push(on('market', drawMarket));
  showRoute(false);
}

function showRoute(moveFocus) {
  if (!shell) return;
  const { main: id, sub } = routeFromHash();
  const route = ROUTES.find((r) => r[0] === id) || ROUTES[1];
  stopView();
  window.scrollTo(0, 0);
  drawNav(id);
  setTitle(ROUTE_TITLES[sub || id]);
  try {
    destroyView = route[2](shell.main, sub) || null;
  } catch (e) {
    console.error(e);
    mount(shell.main, h('div', { class: 'page' }, h('div', { class: 'panel empty' },
      h('strong', null, 'This page could not load'), 'Reload the page and try again.')));
  }
  if (moveFocus) shell.main.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- logged-out screen
function renderLoggedOut() {
  stopView();
  themeBtn = null;
  setTitle('');
  const view = h('div', { id: 'main' });
  mount(root, view);
  destroyView = renderAuth(view, async () => {
    stopView();
    try { await refreshAccount(); } catch (_) { /* the poller will retry */ }
    connectPrices();
    startAccountPolling();
    renderLoggedIn();
  });
}

// ---------------------------------------------------------------- boot
async function boot() {
  setUnauthorizedHandler(() => {
    if (store.me && store.me.user) endSession('Your session ended. Please log in again.');
  });
  window.addEventListener('hashchange', () => showRoute(true));

  try {
    await Promise.all([loadMe(), loadMarkets()]);
  } catch (e) {
    mount(root, h('div', { class: 'page page-narrow' }, h('div', { class: 'panel empty' },
      h('strong', null, 'Could not reach the server'), 'Check your connection and reload the page.')));
    return;
  }
  if (store.me.user || store.me.demo) connectPrices();   // live prices are private: only after login
  startMarketPolling();
  if (store.me.user) {
    try { await refreshAccount(); } catch (_) { setAccount(null); }
    startAccountPolling();
    renderLoggedIn();
  } else {
    renderLoggedOut();
  }
}

boot();
