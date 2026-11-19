const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function editor() {
  const elements = new Map();
  const requests = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: false, required: false, dataset: {}, textContent: '',
      listeners: {},
      addEventListener(event, handler) { this.listeners[event] = handler; },
      setAttribute() {}, append() {}, replaceChildren() {}, focus() {}, close() {},
      reportValidity() { return Boolean(this.value); },
      querySelectorAll() { return [...elements.values()]; }
    });
    return elements.get(id);
  }
  const document = {
    getElementById: element,
    querySelector: () => ({dataset: {project: 'test-project'}}),
    querySelectorAll: () => [],
    createElement: () => element(Symbol()),
    createTextNode: text => ({textContent: text})
  };
  const storage = new Map([['mock-server.push.token:test-project', 'saved-device-token']]);
  Object.entries({'push-scope': 'token', 'push-topic': 'all_users', 'push-type': 'Incident',
    'push-extra-data': '{}', 'push-title': 'Title', 'push-body': 'Body', 'push-pointer': 'pointer'}).forEach(([id, value]) => { element(id).value = value; });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/static/push.js'), 'utf8'), {
    document, localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    URLSearchParams,
    fetch: async (url, options) => {
      if (options?.method === 'POST') {
        requests.push(JSON.parse(options.body.get('payload')));
        return {ok: true, json: async () => ({ok: true, name: 'message-id', history_saved: true})};
      }
      return {ok: true, json: async () => ({entries: []})};
    }
  });
  async function event(id, name) { await element(id).listeners[name]({preventDefault() {}}); }
  return {element, event, requests, storage};
}

test('scope switches send exactly one target and retain the saved device', async () => {
  const ui = editor();
  ui.element('push-scope').value = 'topic';
  await ui.event('push-scope', 'change');
  assert.equal(ui.element('push-token').required, false);
  assert.equal(ui.element('push-topic').required, true);
  assert.equal(ui.element('push-device-field').hidden, true);
  await ui.event('push-form', 'submit');
  assert.equal(ui.requests[0].message.topic, 'all_users');
  assert.equal('token' in ui.requests[0].message, false);
  assert.match(ui.element('push-target').textContent, /all_users/);
  ui.element('push-scope').value = 'token';
  await ui.event('push-scope', 'change');
  await ui.event('push-form', 'submit');
  assert.equal(ui.requests[1].message.token, 'saved-device-token');
  assert.equal('topic' in ui.requests[1].message, false);
});

test('topic JSON round trip preserves options and switching replaces the target', async () => {
  const ui = editor();
  await ui.event('mode-json', 'click');
  ui.element('push-json').value = JSON.stringify({message: {topic: 'other_topic', notification: {title: 'Topic title'}, data: {source: 'Incident', sourceId: 'pointer', custom: 'value'}, android: {priority: 'high'}}});
  await ui.event('mode-form', 'click');
  assert.equal(ui.element('push-scope').value, 'topic');
  assert.equal(ui.element('push-topic').value, 'other_topic');
  assert.equal(ui.element('push-token').value, 'saved-device-token');
  await ui.event('mode-json', 'click');
  const message = JSON.parse(ui.element('push-json').value).message;
  assert.deepEqual(message.android, {priority: 'high'});
  assert.equal(message.data.custom, 'value');
  assert.equal('token' in message, false);
  await ui.event('mode-form', 'click');
  ui.element('push-scope').value = 'token';
  await ui.event('push-scope', 'change');
  await ui.event('push-form', 'submit');
  assert.equal(ui.requests[0].message.token, 'saved-device-token');
  assert.equal('topic' in ui.requests[0].message, false);
  assert.deepEqual(ui.requests[0].message.android, {priority: 'high'});
});

test('ambiguous JSON stays in the editor and cannot send', async () => {
  const ui = editor();
  await ui.event('mode-json', 'click');
  ui.element('push-json').value = JSON.stringify({message: {token: 'device', topic: 'all_users', notification: {title: 'Title'}}});
  await ui.event('mode-form', 'click');
  assert.equal(ui.element('push-json-panel').hidden, false);
  assert.match(ui.element('push-result').textContent, /只能填写 token 或 topic/);
  await ui.event('push-form', 'submit');
  assert.equal(ui.requests.length, 0);
});
