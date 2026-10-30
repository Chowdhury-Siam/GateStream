import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker from './worker.mjs';

const env = { ORIGIN_URL: 'https://origin.example' };

test('streams a range response unchanged and preserves path, query and request headers', async (t) => {
  let controller;
  const body = new ReadableStream({ start(value) { controller = value; } });
  const upstream = new Response(body, {
    status: 206,
    headers: { 'Content-Range': 'bytes 10-19/100', 'Content-Length': '10', 'Accept-Ranges': 'bytes' },
  });
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'https://origin.example/dl/code?stream=1');
    assert.equal(options.headers.get('Range'), 'bytes=10-19');
    assert.equal(options.headers.get('If-Range'), '"etag"');
    assert.equal(options.headers.get('Connection'), null);
    assert.equal(options.redirect, 'manual');
    return upstream;
  });
  const response = await worker.fetch(new Request('https://proxy.example/dl/code?stream=1', {
    headers: { Range: 'bytes=10-19', 'If-Range': '"etag"', Connection: 'keep-alive' },
  }), env);
  assert.equal(response, upstream); // Returns before any body chunks exist: no whole-file buffering.
  controller.enqueue(new TextEncoder().encode('0123456789'));
  controller.close();
  assert.equal(await response.text(), '0123456789');
});

test('forwards HEAD and does not turn a // path into a different upstream host', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(new URL(url).host, 'origin.example');
    assert.equal(new URL(url).pathname, '//other.example/dl/code');
    assert.equal(options.method, 'HEAD');
    return new Response(null, { status: 200, headers: { 'Content-Length': '100' } });
  });
  const response = await worker.fetch(new Request('https://proxy.example//other.example/dl/code', { method: 'HEAD' }), env);
  assert.equal(response.headers.get('Content-Length'), '100');
});

test('rejects missing, malformed and self-referencing origins without fetching', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', () => { throw new Error('Must not fetch'); });
  for (const ORIGIN_URL of [undefined, 'bad', 'http://origin.example', 'https://proxy.example',
    'https://origin.example/path', 'https://user:pass@origin.example', 'https://origin.example?x=1']) {
    const response = await worker.fetch(new Request('https://proxy.example/'), { ORIGIN_URL });
    assert.equal(response.status, 503);
  }
  assert.equal(fetch.mock.callCount(), 0);
});

test('handles preflight locally and rejects unsupported methods', async () => {
  const response = await worker.fetch(new Request('https://proxy.example/dl/code', { method: 'OPTIONS' }), env);
  assert.equal(response.status, 204);
  assert.match(response.headers.get('Access-Control-Allow-Headers'), /Range/);
  assert.equal((await worker.fetch(new Request('https://proxy.example/', { method: 'POST' }), env)).status, 405);
});

test('preserves origin errors and redirects; reports network failures as 502', async (t) => {
  for (const status of [404, 416, 302]) {
    t.mock.method(globalThis, 'fetch', async () => new Response(null, { status }));
    assert.equal((await worker.fetch(new Request('https://proxy.example/dl/code'), env)).status, status);
    t.mock.restoreAll();
  }
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('Network failure'); });
  assert.equal((await worker.fetch(new Request('https://proxy.example/health'), env)).status, 502);
});

test('forwards catalog POST forms, cookies and body without buffering ZIP responses', async (t) => {
  for (const path of ['/index/login', '/index/logout', '/index/download']) {
    const upstream = new Response('zip-or-login', { headers: { 'Set-Cookie': 'gatestream_index=session; HttpOnly', 'Cache-Control': 'private, no-store' } });
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      assert.equal(url, 'https://origin.example' + path);
      assert.equal(options.method, 'POST');
      assert.equal(options.headers.get('Cookie'), 'gatestream_index_csrf=token');
      assert.equal(await new Response(options.body).text(), 'csrf=token&code=episode1');
      return upstream;
    });
    const response = await worker.fetch(new Request('https://proxy.example' + path, {
      method: 'POST', headers: { Cookie: 'gatestream_index_csrf=token', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'csrf=token&code=episode1',
    }), env);
    assert.equal(response, upstream);
    assert.match(response.headers.get('Set-Cookie'), /HttpOnly/);
    t.mock.restoreAll();
  }
  assert.equal((await worker.fetch(new Request('https://proxy.example/dl/code', { method: 'POST' }), env)).status, 405);
});

test('anonymous download grouping survives separate ranges and overwrites caller keys', async (t) => {
  const keys = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const key = options.headers.get('X-GateStream-Client');
    assert.match(key, /^[a-f0-9]{64}$/);
    assert.notEqual(key, 'caller-controlled');
    keys.push(key);
    return new Response('part');
  });
  for (const [address, agent, range] of [
    ['192.0.2.1', 'Manager', 'bytes=0-'],
    ['192.0.2.1', 'Manager', 'bytes=200-'],
    ['192.0.2.2', 'Manager', 'bytes=0-'],
    ['192.0.2.1', 'OtherManager', 'bytes=0-'],
  ]) {
    await worker.fetch(new Request('https://proxy.example/dl/file', {headers: {
      'CF-Connecting-IP': address, 'User-Agent': agent, 'Range': range,
      'X-GateStream-Client': 'caller-controlled',
    }}), env);
  }
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[0], keys[2]);
  assert.notEqual(keys[0], keys[3]);
});

test('caller grouping key is stripped when no edge visitor address exists', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(options.headers.get('X-GateStream-Client'), null);
    return new Response('part');
  });
  await worker.fetch(new Request('https://proxy.example/dl/file', {headers: {
    'X-GateStream-Client': 'caller-controlled',
  }}), env);
});
