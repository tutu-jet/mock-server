const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function ui({textWidth = 500, available = 200} = {}) {
  const listeners = {};
  const classes = new Set();
  const styles = {};
  const actions = [];
  let selection = '';
  const title = {
    clientWidth: available,
    focused: false, hovered: false,
    querySelector: () => ({scrollWidth: textWidth}),
    classList: {add: name => classes.add(name), remove: name => classes.delete(name)},
    style: {setProperty: (key, value) => { styles[key] = value; }},
    contains: target => target === title || target?.insideTitle,
    matches: selector => selector === ':focus-visible' ? title.focused : title.hovered,
    click: () => actions.push('detail'),
  };
  const card = {querySelector: selector => selector === '.entity-name' ? title : {click: () => actions.push('edit')}};
  const target = (interactive = false, insideTitle = false) => ({
    insideTitle,
    closest(selector) {
      if (selector === '.entity-card') return card;
      if (selector === '.entity-name') return insideTitle ? title : null;
      return interactive ? {} : null;
    },
  });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/static/entity-row.js'), 'utf8'), {
    document: {body: {addEventListener: (name, callback) => { listeners[name] = callback; }}},
    window: {getSelection: () => ({toString: () => selection})},
  });
  return {listeners, classes, styles, actions, title, target, select: text => { selection = text; }};
}

test('row background opens details, controls and text selection do not trigger row action', () => {
  const view = ui();
  view.listeners.click({target: view.target(), button: 0});
  assert.deepEqual(view.actions, ['detail']);
  view.listeners.click({target: view.target(true), button: 0});
  view.listeners.click({target: view.target(), button: 0, defaultPrevented: true});
  view.listeners.click({target: view.target(), button: 0, ctrlKey: true});
  view.listeners.click({target: view.target(), button: 1});
  view.select('selected title');
  view.listeners.click({target: view.target(), button: 0});
  assert.deepEqual(view.actions, ['detail']);
});

test('overflowing titles scroll only the hidden distance and reset on leaving', () => {
  const view = ui();
  const target = view.target(true, true);
  view.listeners.pointerover({target, relatedTarget: null});
  assert.equal(view.classes.has('is-scrolling'), true);
  assert.equal(view.styles['--entity-title-offset'], '-300px');
  assert.equal(view.styles['--entity-title-duration'], (300 / 35) + 's');
  view.listeners.pointerout({target, relatedTarget: {insideTitle: true}});
  assert.equal(view.classes.has('is-scrolling'), true);
  view.listeners.pointerout({target, relatedTarget: null});
  assert.equal(view.classes.has('is-scrolling'), false);
});

test('titles that fit remain stationary', () => {
  const view = ui({textWidth: 100, available: 200});
  view.listeners.pointerover({target: view.target(true, true), relatedTarget: null});
  assert.equal(view.classes.has('is-scrolling'), false);
  assert.deepEqual(view.styles, {});
});

test('keyboard focus also reveals a long title and blur resets it', () => {
  const view = ui();
  const target = view.target(true, true);
  view.title.focused = true;
  view.listeners.focusin({target});
  assert.equal(view.classes.has('is-scrolling'), true);
  view.listeners.pointerout({target, relatedTarget: null});
  assert.equal(view.classes.has('is-scrolling'), true);
  view.title.focused = false;
  view.listeners.focusout({target});
  assert.equal(view.classes.has('is-scrolling'), false);
});
