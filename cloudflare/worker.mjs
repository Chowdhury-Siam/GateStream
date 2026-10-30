export default {
  async fetch(request, env) {
    let origin;
    try {
      origin = new URL(env.ORIGIN_URL);
      if (origin.protocol !== 'https:' || origin.username || origin.password ||
          origin.pathname !== '/' || origin.search || origin.hash ||
          origin.host === new URL(request.url).host) {
        throw new Error('Invalid origin');
      }
    } catch {
      return new Response('Configure ORIGIN_URL as the HTTPS origin server, not this Worker.', { status: 503 });
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
          'Access-Control-Allow-Headers': 'Range, If-Range, If-None-Match, If-Modified-Since',
          'Access-Control-Max-Age': '86400',
        },
      });
    }
    const incoming = new URL(request.url);
    const formPost = request.method === 'POST' &&
      ['/index/login', '/index/logout', '/index/download'].includes(incoming.pathname);
    if (!['GET', 'HEAD'].includes(request.method) && !formPost) {
      return new Response('Method not allowed', {
        status: 405, headers: { Allow: 'GET, HEAD, OPTIONS' },
      });
    }

    // Assign the path separately: a path beginning with // must not change the host.
    origin.pathname = incoming.pathname;
    origin.search = incoming.search;
    const headers = new Headers(request.headers);
    for (const name of ['Host', 'Connection', 'Keep-Alive', 'Transfer-Encoding', 'Upgrade']) {
      headers.delete(name);
    }
    // Anonymous analytics key, not an auth credential. Overwrite any caller
    // value and preserve visitor grouping across changing proxy connections.
    headers.delete('X-GateStream-Client');
    const visitor = request.headers.get('CF-Connecting-IPv6') || request.headers.get('CF-Connecting-IP');
    if (visitor && (incoming.pathname.startsWith('/dl/') || incoming.pathname.startsWith('/index/archive/'))) {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(
        `${visitor}|${request.headers.get('User-Agent') || ''}`));
      headers.set('X-GateStream-Client', Array.from(new Uint8Array(digest),
        value => value.toString(16).padStart(2, '0')).join(''));
    }
    try {
      // Pass the response through without reading the body or changing range headers.
      return await fetch(origin.toString(), {
        method: request.method,
        headers,
        ...(formPost ? { body: request.body } : {}),
        redirect: 'manual',
      });
    } catch {
      return new Response('Origin server is unavailable', { status: 502 });
    }
  },
};
