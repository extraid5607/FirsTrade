import { h, mount } from '../util.js';
import { renderChain } from './chain.js';
import { renderJournal } from './journal.js';
import { renderStats } from './stats.js';
import { renderRules } from './rules.js';
import { renderLeaderboard } from './leaderboard.js';

const TABS = [
  ['chain', 'Option chain', renderChain],
  ['journal', 'Journal', renderJournal],
  ['stats', 'Stats', renderStats],
  ['rules', 'Rules', renderRules],
  ['leaderboard', 'Leaderboard', renderLeaderboard],
];

export function renderTools(root, initialSub) {
  let active = initialSub || 'chain';
  let subDestroy = null;

  const seg = h('div', { class: 'seg tools-seg', role: 'tablist', 'aria-label': 'Trading Tools' });
  const content = h('div', { class: 'tools-content' });

  function switchTab(k) {
    if (subDestroy) {
      try { subDestroy(); } catch (e) { console.error(e); }
      subDestroy = null;
    }
    active = k;
    drawSeg();
    mount(content);
    const item = TABS.find((t) => t[0] === k) || TABS[0];
    try {
      subDestroy = item[2](content) || null;
    } catch (e) {
      console.error(e);
      mount(content, h('div', { class: 'empty' }, 'Could not load tool view.'));
    }
  }

  function drawSeg() {
    mount(seg, TABS.map(([k, label]) => h('button', {
      type: 'button',
      class: 'seg-btn',
      role: 'tab',
      'aria-selected': String(active === k),
      'aria-pressed': String(active === k),
      on: { click: () => switchTab(k) },
    }, label)));
  }

  mount(root, h('div', { class: 'page' },
    h('div', { class: 'tools-header' },
      h('h1', { class: 'tools-title' }, 'Tools & Analysis'),
      seg),
    content));

  drawSeg();
  switchTab(active);

  return function destroy() {
    if (subDestroy) {
      try { subDestroy(); } catch (e) { console.error(e); }
      subDestroy = null;
    }
  };
}
