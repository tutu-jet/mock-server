const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../web/templates/base.html'), 'utf8');
const source = template.slice(template.indexOf('function setBodyMode('), template.indexOf('// 复制流量原始 body'));
function controls(writeText) {
  const events = [];
  const context = {
    navigator: {clipboard: {writeText}},
    document: {body: {dispatchEvent: event => events.push(event)}},
    CustomEvent: class { constructor(name, options) { this.type = name; this.detail = options.detail; } },
  };
  vm.runInNewContext(source, context);
  return {context, events};
}

test('mode switches only its own viewer and updates visibility and pressed state', () => {
  const {context} = controls();
  function viewer() {
    const panels = ['reading', 'json'].map(mode => ({dataset: {bodyPanel: mode}, hidden: mode === 'json'}));
    const buttons = ['reading', 'json'].map(mode => ({dataset: {bodyMode: mode}, attrs: {'aria-pressed': String(mode === 'reading')},
      setAttribute(key, value) { this.attrs[key] = value; }, closest() { return root; }}));
    const root = {querySelectorAll(selector) { return selector === '[data-body-mode]' ? buttons : panels; }};
    return {panels, buttons};
  }
  const response = viewer();
  const other = viewer();
  context.setBodyMode(response.buttons[1]);
  assert.deepEqual(response.panels.map(panel => panel.hidden), [true, false]);
  assert.deepEqual(response.buttons.map(button => button.attrs['aria-pressed']), ['false', 'true']);
  assert.deepEqual(other.panels.map(panel => panel.hidden), [false, true]);
  context.setBodyMode(response.buttons[0]);
  assert.deepEqual(response.panels.map(panel => panel.hidden), [false, true]);
});

test('field copy preserves exact fences, CRLF and literal backslashes', async () => {
  const fence = String.fromCharCode(96).repeat(3);
  const raw = '\r\n' + fence + 'list-show\r\n{"path":"C:\\temp"}\r\n' + fence;
  let copied;
  const {context, events} = controls(async text => { copied = text; });
  const button = {dataset: {copyContent: JSON.stringify(raw)}, disabled: false};
  await context.copyBodyContent(button);
  assert.equal(copied, raw);
  assert.equal(button.disabled, false);
  assert.equal(events[0].detail.kind, 'success');
});

test('clipboard failure restores button and reports error', async () => {
  const {context, events} = controls(async () => { throw new Error('Permission denied'); });
  const button = {dataset: {copyContent: JSON.stringify('example')}, disabled: false};
  await context.copyBodyContent(button);
  assert.equal(button.disabled, false);
  assert.equal(events[0].detail.kind, 'error');
  assert.match(events[0].detail.msg, /Permission denied/);
});
