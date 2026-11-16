/* Form and JSON share one payload; history resends only on an explicit click. */
(() => {
  const $ = id => document.getElementById(id);
  const project = document.querySelector('.push-workspace').dataset.project;
  const types = {
    Incident: {label: '案例', title: '案例通知测试', body: '点击查看案例详情', pointer: '案例 pointer', route: '案例详情', note: '点击通知打开案例详情。测试 noauth 时，让案例详情接口返回 403。', required: true},
    'CAC Event': {label: '云事件', title: '云事件通知测试', body: '点击查看云事件详情', pointer: '事件 ID', route: '云事件详情', note: '点击通知打开云事件详情。测试 noauth 时，让事件详情头部接口返回 403。', required: true},
    CAC: {label: '云系统', title: '云系统通知测试', body: '云系统可用性状态更新', pointer: '系统编号', route: '未配置详情路由', note: '当前 Android 系统推送路由不处理 CAC 的详情跳转；可测试通知展示。', required: false},
    Community: {label: '社区', title: '社区通知测试', body: '你有一条新的社区通知', pointer: 'sourceId（可选）', route: '未配置详情路由', note: '当前 Android 系统推送路由不处理 Community 的详情跳转；可测试通知展示。', required: false},
    custom: {label: '自定义', title: '测试通知', body: '一条自定义测试通知', pointer: 'sourceId（可选）', route: '由 App 决定', note: '填写自定义 source。未识别的类型不会打开详情。', required: false}
  };
  const outcomes = {accepted: 'FCM 已接受', failed: '发送失败', unknown: '结果未知'};
  let mode = 'form';
  let base = {message: {token: '', notification: {}, data: {}}};
  let entries = [];
  let lastType = 'Incident';
  let historyError = '';
  let sending = false;
  function show(text, status = 'failed') {
    ['push-result', 'history-result'].forEach(id => {
      $(id).hidden = false;
      $(id).textContent = text;
      $(id).dataset.status = status;
    });
  }
  function resendAction(id) {
    const button = action('重新发送', () => resendEntry(id));
    button.dataset.resend = String(id);
    button.disabled = sending;
    return button;
  }
  function setSending(value) {
    sending = value;
    $('push-send').disabled = value;
    document.querySelectorAll('[data-resend]').forEach(button => { button.disabled = value; });
  }
  function showTab(name) {
    document.querySelectorAll('[data-tab]').forEach(button => {
      const active = button.dataset.tab === name;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      $('panel-' + button.dataset.tab).hidden = !active;
    });
    if (name === 'history') refreshHistory();
  }
  document.querySelectorAll('[data-tab]').forEach((button, index, buttons) => {
    button.addEventListener('click', () => showTab(button.dataset.tab));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus(); showTab(buttons[next].dataset.tab);
    });
  });
  document.querySelectorAll('[data-help]').forEach(button => button.addEventListener('click', () => {
    showTab('help'); $(button.dataset.help).scrollIntoView({block: 'start'});
  }));
  document.querySelectorAll('[data-open-history]').forEach(button => button.addEventListener('click', () => showTab('history')));
  function typeUI() {
    const type = types[$('push-type').value] || types.custom;
    $('custom-source-field').hidden = $('push-type').value !== 'custom';
    $('push-source').required = ! $('custom-source-field').hidden;
    $('pointer-label').textContent = type.pointer + (type.required ? ' · 必填' : '');
    $('push-pointer').required = type.required;
    $('push-pointer').placeholder = type.required ? '填写目标 ID；403 Mock 可使用测试 ID' : '按需填写';
    $('push-route-note').textContent = type.note;
  }
  function object(value, label) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(label + ' 必须是 JSON 对象');
    return value;
  }
  function formPayload() {
    const extra = object(JSON.parse($('push-extra-data').value || '{}'), '附加 data');
    if (Object.values(extra).some(value => typeof value !== 'string')) throw new Error('附加 data 的值必须是字符串');
    const source = $('push-type').value === 'custom' ? $('push-source').value.trim() : $('push-type').value;
    const value = JSON.parse(JSON.stringify(base));
    value.message.token = $('push-token').value.replace(/\s/g, '');
    value.message.notification = {...value.message.notification, title: $('push-title').value, body: $('push-body').value};
    value.message.data = {...extra, source, sourceId: $('push-pointer').value.trim()};
    return value;
  }
  function readPayload() {
    const value = mode === 'form' ? formPayload() : JSON.parse($('push-json').value);
    object(value, '消息'); object(value.message, 'message');
    return value;
  }
  function fill(value) {
    object(value, '消息'); const message = object(value.message, 'message');
    const data = object(message.data || {}, 'data');
    if (Object.values(data).some(item => typeof item !== 'string')) throw new Error('data 的值必须是字符串');
    const notification = object(message.notification || {}, 'notification');
    if (typeof message.token !== 'string') throw new Error('token 必须是字符串');
    if (['title', 'body'].some(key => key in notification && typeof notification[key] !== 'string')) throw new Error('标题和正文必须是字符串');
    base = JSON.parse(JSON.stringify(value));
    $('push-token').value = message.token;
    $('push-title').value = notification.title || '';
    $('push-body').value = notification.body || '';
    $('push-type').value = types[data.source] && data.source !== 'custom' ? data.source : 'custom';
    $('push-source').value = data.source || '';
    $('push-pointer').value = data.sourceId || '';
    const extra = {...data}; delete extra.source; delete extra.sourceId;
    $('push-extra-data').value = JSON.stringify(extra, null, 2);
    lastType = $('push-type').value;
    typeUI();
  }
  function preview() {
    try {
      const message = readPayload().message;
      const source = message.data?.source || '';
      const type = types[source] || types.custom;
      $('preview-title').textContent = message.notification?.title || '通知标题';
      $('preview-body').textContent = message.notification?.body || '通知正文';
      $('preview-source').textContent = source || '自定义';
      $('preview-route').textContent = type.route;
      const token = message.token || '';
      $('push-target').textContent = token ? '单设备 · …' + token.slice(-8) : '尚未填写设备 Token';
    } catch (error) {
      $('push-target').textContent = '请检查 JSON 格式';
    }
  }
  function switchMode(next) {
    if (next === mode) return;
    try {
      if (next === 'json') $('push-json').value = JSON.stringify(formPayload(), null, 2);
      else fill(readPayload());
    } catch (error) { show('无法切换：' + error.message); return; }
    mode = next;
    $('push-fields').hidden = mode !== 'form';
    $('push-json-panel').hidden = mode !== 'json';
    $('mode-form').setAttribute('aria-pressed', String(mode === 'form'));
    $('mode-json').setAttribute('aria-pressed', String(mode === 'json'));
    preview();
  }
  $('mode-form').addEventListener('click', () => switchMode('form'));
  $('mode-json').addEventListener('click', () => switchMode('json'));
  $('push-type').addEventListener('change', () => {
    const old = types[lastType]; const next = types[$('push-type').value];
    if ($('push-title').value === old.title) $('push-title').value = next.title;
    if ($('push-body').value === old.body) $('push-body').value = next.body;
    if (lastType !== $('push-type').value) $('push-pointer').value = '';
    lastType = $('push-type').value; typeUI(); preview();
  });
  $('push-token').addEventListener('blur', () => { $('push-token').value = $('push-token').value.replace(/\s/g, ''); preview(); });
  $('push-form').addEventListener('input', preview);
  $('push-format').addEventListener('click', () => {
    try { $('push-json').value = JSON.stringify(JSON.parse($('push-json').value), null, 2); preview(); }
    catch (error) { show('JSON 格式错误：' + error.message); }
  });
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function badge(status) { return node('span', outcomes[status] || '结果未知', 'push-status push-status-' + status); }
  function date(entry) { return new Date(entry.created_at * 1000).toLocaleString('zh-CN', {hour12: false}); }
  function action(text, handler) {
    const button = node('button', text, 'push-text-button'); button.type = 'button'; button.addEventListener('click', handler); return button;
  }
  function renderHistory() {
    $('history-count').textContent = entries.length;
    const list = $('push-history-list'); list.replaceChildren();
    const search = $('history-search').value.toLowerCase();
    const type = $('history-type').value; const status = $('history-status').value;
    const filtered = entries.filter(entry => (!search || [entry.title, entry.body, entry.source_id, entry.device].join(' ').toLowerCase().includes(search)) && (!type || (type === 'custom' ? !types[entry.source] : entry.source === type)) && (!status || entry.status === status));
    if (historyError || !filtered.length) {
      const empty = node('div', undefined, 'push-empty');
      empty.append(node('strong', historyError ? '暂时无法读取历史' : entries.length ? '没有匹配的记录' : '还没有发送记录'), node('p', historyError || (entries.length ? '试试其他搜索条件。' : '发出第一条通知后，结果会保存在这里。')));
      list.append(empty);
    } else {
      filtered.forEach(entry => {
        const row = node('article', undefined, 'push-history-row');
        const content = node('div', undefined, 'push-history-content');
        const head = node('div', undefined, 'push-history-heading'); head.append(node('strong', entry.title || '无标题通知'), badge(entry.status));
        content.append(head, node('p', entry.body || '无正文', 'push-history-body'));
        const meta = node('div', undefined, 'push-history-meta');
        [entry.source || '未指定类型', entry.source_id || '无目标 ID', entry.device, date(entry)].forEach(text => meta.append(node('span', text)));
        content.append(meta);
        const actions = node('div', undefined, 'push-history-actions');
        actions.append(action('详情', () => showDetail(entry.id)), action('载入编辑', () => loadEntry(entry.id)), resendAction(entry.id));
        row.append(content, actions); list.append(row);
      });
    }
    const recent = $('push-recent'); recent.replaceChildren();
    if (!entries.length || historyError) recent.append(node('p', historyError || '尚无记录。发送后的结果会保存在本机。', 'push-empty-small'));
    else entries.slice(0, 3).forEach(entry => {
      const item = node('div', undefined, 'push-recent-item');
      const line = node('div', undefined, 'push-recent-line'); line.append(node('strong', entry.title || '无标题通知'), badge(entry.status));
      item.append(line, node('p', (entry.source || '自定义') + ' · ' + date(entry)), action('载入编辑 →', () => loadEntry(entry.id)), document.createTextNode(' · '), resendAction(entry.id)); recent.append(item);
    });
  }
  async function refreshHistory() {
    try {
      const response = await fetch('/push/history'); const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '读取失败');
      entries = data.entries; historyError = '';
    } catch (error) { historyError = error.message; }
    renderHistory();
  }
  async function detail(id) {
    const response = await fetch('/push/history/' + id); const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '读取失败'); return data;
  }
  async function loadEntry(id) {
    try {
      const entry = await detail(id); fill(entry.payload);
      mode = 'form'; $('push-fields').hidden = false; $('push-json-panel').hidden = true;
      $('mode-form').setAttribute('aria-pressed', 'true'); $('mode-json').setAttribute('aria-pressed', 'false');
      $('history-dialog').close(); showTab('compose'); preview();
      show('已载入历史记录，请检查设备和目标 ID 后点击发送。' + (entry.project !== project ? ' 原项目为 ' + entry.project + '，本次发送将使用 ' + project + '。' : ''), 'info');
      $('push-token').focus();
    } catch (error) { showTab('compose'); show(error.message); }
  }
  async function showDetail(id) {
    try {
      const entry = await detail(id); const content = $('history-detail'); content.replaceChildren();
      content.append(badge(entry.status), node('p', date(entry) + ' · ' + entry.project), node('p', entry.result, 'push-detail-result'));
      const display = JSON.parse(JSON.stringify(entry.payload)); display.message.token = '[' + entry.device + '，完整 Token 在载入编辑时恢复]';
      content.append(node('pre', JSON.stringify(display, null, 2)), action('载入此记录编辑', () => loadEntry(id)), document.createTextNode(' · '), resendAction(id));
      $('history-dialog').showModal();
    } catch (error) { showTab('compose'); show(error.message); }
  }
  $('history-close').addEventListener('click', () => $('history-dialog').close());
  $('history-dialog').addEventListener('click', event => { if (event.target === $('history-dialog')) $('history-dialog').close(); });
  $('history-refresh').addEventListener('click', refreshHistory);
  ['history-search', 'history-type', 'history-status'].forEach(id => $(id).addEventListener('input', renderHistory));
  $('push-form').addEventListener('submit', async event => {
    event.preventDefault(); if (sending) return;
    if (mode === 'form') {
      const fields = [...$('push-fields').querySelectorAll('input,textarea')].filter(field => field.required);
      for (const field of fields) if (!field.reportValidity()) return;
    }
    let payload;
    try { payload = readPayload(); }
    catch (error) { show('JSON 格式错误：' + error.message); return; }
    await sendRequest('/push/send', {method: 'POST', body: new URLSearchParams({payload: JSON.stringify(payload)})});
  });
  async function resendEntry(id) {
    if (sending) return;
    $('history-dialog').close();
    await sendRequest('/push/history/' + id + '/resend', {method: 'POST'});
  }
  async function sendRequest(url, options) {
    if (sending) return;
    setSending(true); show('正在发送到此设备…', 'info');
    try {
      const response = await fetch(url, options);
      const data = await response.json();
      const warning = data.history_id && !data.history_saved ? ' 历史结果未能更新，请勿因记录状态而重复发送。' : '';
      show((data.ok ? 'FCM 已接受消息：' + data.name + '。请在手机确认是否收到。' : data.error || data.detail || '发送失败') + warning, data.ok ? 'accepted' : data.outcome || 'failed');
    } catch (error) { show('连接失败，结果未知。请先检查手机是否收到，再决定是否重发。', 'unknown'); }
    finally { setSending(false); await refreshHistory(); }
  }
  typeUI(); preview(); refreshHistory();
})();
