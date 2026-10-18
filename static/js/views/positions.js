import { h, mount } from '../util.js';
import { createDock } from '../dock.js';

export function renderPositions(root) {
  const dockPanel = h('div', { class: 'panel dock' });
  mount(root, h('div', { class: 'page' },
    h('div', { class: 'page-head' },
      h('h1', null, 'Positions & Orders'),
      h('p', { class: 'page-sub' }, 'Monitor your open trades, active limit orders, and past trade history.')),
    dockPanel));

  const dock = createDock(dockPanel);
  return function destroy() {
    dock.destroy();
  };
}
