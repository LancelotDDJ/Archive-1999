/* API 客户端：统一 fetch 封装（Bearer 注入 + 401 自动刷新重放）。 */
import { Auth } from './auth.js';

const API_BASE = '/api/v1';
let _refreshing = null;

async function rawFetch(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const tok = Auth.accessToken();
  if (tok) headers['Authorization'] = `Bearer ${tok}`;
  if (opts.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  return fetch(API_BASE + path, { ...opts, headers });
}

async function request(path, opts = {}, _retried = false) {
  let resp = await rawFetch(path, opts);
  if (resp.status === 401 && !_retried && Auth.refreshToken()) {
    await (_refreshing ??= Auth.refresh().finally(() => { _refreshing = null; }));
    resp = await rawFetch(path, opts);
  }
  if (resp.status === 401) {
    Auth.clear();
    location.href = '/login';
    throw new Error('unauthorized');
  }
  let data = null;
  const text = await resp.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!resp.ok) {
    const err = new Error(data?.error?.message || `HTTP ${resp.status}`);
    err.code = data?.error?.code || 'http_' + resp.status;
    err.status = resp.status;
    throw err;
  }
  return data;
}

/** NDJSON 流式请求：逐行回调事件对象。 */
async function stream(path, body, onEvent, signal) {
  const doFetch = () => rawFetch(path, { method: 'POST', json: body, signal });
  let resp = await doFetch();
  if (resp.status === 401 && Auth.refreshToken()) {
    await (_refreshing ??= Auth.refresh().finally(() => { _refreshing = null; }));
    resp = await doFetch();
  }
  if (resp.status === 401) { Auth.clear(); location.href = '/login'; return; }
  if (!resp.ok || !resp.body) {
    let msg = `HTTP ${resp.status}`;
    try { msg = (await resp.json())?.error?.message || msg; } catch {}
    onEvent({ type: 'error', error: msg });
    return;
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (!line) continue;
      try { onEvent(JSON.parse(line)); } catch {}
    }
  }
}

export const Api = { request, stream };
