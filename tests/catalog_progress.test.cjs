const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../bot/server/templates/catalog.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1];
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
function response(data, status = 200) {
  return {ok: status >= 200 && status < 300, status, json: async () => data, text: async () => String(data)};
}
function page(fetch, mode = 'prepare') {
  const nodes = new Map(), timers = new Map();
  let timer = 0;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      hidden: false, disabled: false, checked: true, textContent: '', listeners: {}, clicks: 0,
      addEventListener(event, callback) { this.listeners[event] = callback; },
      removeAttribute(name) { delete this[name]; },
      click() { this.clicks++; },
    });
    return nodes.get(id);
  };
  const choices = [node('first'), node('second')];
  node('selection').action = '/index/download';
  node('zip-name').value = 'My episodes';
  node('zip-mode').value = mode;
  const context = {
    document: {getElementById: node, querySelectorAll: () => choices, hidden: false},
    window: {addEventListener() {}}, navigator: {}, AbortController,
    FormData: class {
      constructor() { assert.equal(node('zip-name').disabled, false); }
      get(key) { return key === 'mode' ? mode : node('zip-name').value; }
    },
    setTimeout(fn, delay) { const id = ++timer; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); }, fetch,
  };
  vm.createContext(context); vm.runInContext(source, context);
  return {
    node, context,
    submit: () => node('selection').listeners.submit({preventDefault() {}, currentTarget: node('selection')}),
    fire(delay) {
      for (const [id, item] of [...timers]) if (item.delay === delay) {
        timers.delete(id); item.fn();
      }
    },
  };
}
const link = {mode: 'prepare', url: '/index/archive/token/My%20episodes.zip', status_url: '/status'};

test('slow link creation stays indeterminate, then queued and prepared bytes update before download', async () => {
  const create = deferred();
  const states = [
    {state: 'queued', bytes: 0, total: 4 * 1024 ** 2},
    {state: 'preparing', bytes: 1024 ** 2, total: 4 * 1024 ** 2, source_speed_bps: 512 * 1024},
    {state: 'ready', bytes: 4 * 1024 ** 2, total: 4 * 1024 ** 2},
  ];
  const ui = page(async (url, options) => {
    if (url === '/index/download') {
      assert.equal(options.body.get('zip_name'), 'My episodes');
      return create.promise;
    }
    assert.equal(options.cache, 'no-store');
    return response(states.shift());
  });
  const submitted = ui.submit();
  assert.equal(ui.node('zip-progress').hidden, false);
  assert.equal(ui.node('zip-progress-bar').value, undefined);
  assert.match(ui.node('selection-count').textContent, /Checking selected files/);
  ui.fire(10000);
  assert.match(ui.node('zip-progress-detail').textContent, /No preparation progress received/);
  create.resolve(response(link));
  await tick();
  assert.equal(ui.node('zip-progress-bar').value, 0);
  assert.match(ui.node('zip-progress-detail').textContent, /Queued 0% · 0 B \/ 4.00 MiB/);
  assert.equal(ui.node('zip-url').clicks, 0);
  ui.fire(2000); await tick();
  assert.equal(ui.node('zip-progress-bar').value, 25);
  assert.match(ui.node('zip-progress-detail').textContent, /Prepared 25% · 1.00 MiB \/ 4.00 MiB · 512.00 KiB\/s/);
  assert.equal(ui.node('zip-url').clicks, 0);
  ui.fire(2000); await submitted;
  assert.equal(ui.node('zip-progress-bar').value, 100);
  assert.match(ui.node('zip-progress-detail').textContent, /ZIP ready 100%/);
  assert.equal(ui.node('zip-url').clicks, 1);
  assert.equal(ui.node('zip-url').download, 'My episodes.zip');
  assert.equal(ui.node('zip-name').disabled, false);
  ui.node('zip-name').listeners.input();
  assert.equal(ui.node('zip-progress').hidden, true);
});

test('immediate mode starts before status polling and shows buffer bytes', async () => {
  const ui = page(async url => {
    if (url === '/index/download') return response({...link, mode: 'stream'});
    assert.equal(ui.node('zip-url').clicks, 1);
    return response({state: 'ready', bytes: 4096, total: 4096});
  }, 'stream');
  await ui.submit(); await tick();
  assert.equal(ui.node('zip-progress-bar').value, 100);
  assert.match(ui.node('zip-progress-detail').textContent, /4.00 KiB \/ 4.00 KiB/);
});

test('temporary status network failure preserves last measured bytes and reconnects', async () => {
  let check = 0;
  const ui = page(async url => {
    if (url === '/index/download') return response(link);
    if (++check === 1) return response({state: 'preparing', bytes: 1024, total: 2048});
    if (check === 2) throw new TypeError('offline');
    return response({state: 'ready', bytes: 2048, total: 2048});
  });
  const submitted = ui.submit(); await tick();
  ui.fire(2000); await tick();
  assert.match(ui.node('selection-count').textContent, /Reconnecting/);
  assert.match(ui.node('zip-progress-detail').textContent, /Prepared 50%/);
  assert.equal(ui.node('zip-url').clicks, 0);
  ui.fire(2000); await submitted;
  assert.equal(ui.node('zip-url').clicks, 1);
});

test('expired or failed ZIP cannot trigger an automatic download', async () => {
  const ui = page(async url => url === '/index/download' ? response(link) : response('expired', 410));
  await ui.submit();
  assert.equal(ui.node('zip-url').clicks, 0);
  assert.match(ui.node('selection-count').textContent, /failed or the link expired/);
  assert.equal(ui.node('download-selected').disabled, false);
});

test('link-creation timeout releases controls and reports the stalled server', async () => {
  const ui = page((url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('timeout'), {name: 'AbortError'})));
  }));
  const submitted = ui.submit();
  ui.fire(60000); await submitted;
  assert.match(ui.node('selection-count').textContent, /server took too long/);
  assert.equal(ui.node('zip-name').disabled, false);
  assert.equal(ui.node('zip-mode').disabled, false);
  assert.equal(ui.node('zip-url').clicks, 0);
});

test('failed fetch during link creation gives a useful connection error without fake progress', async () => {
  const ui = page(async () => { throw new TypeError('Failed to fetch'); });
  await ui.submit();
  assert.match(ui.node('selection-count').textContent, /Connection to the server was lost/);
  assert.equal(ui.node('zip-progress-bar').value, undefined);
  assert.equal(ui.node('download-selected').disabled, false);
  assert.equal(ui.node('zip-url').clicks, 0);
});

test('server link deadline is displayed without starting a download', async () => {
  const ui = page(async () => response('The server could not create your ZIP link in time. Please try again shortly.', 503));
  await ui.submit();
  assert.match(ui.node('selection-count').textContent, /could not create your ZIP link in time/);
  assert.equal(ui.node('zip-url').clicks, 0);
  assert.equal(ui.node('zip-mode').disabled, false);
});
