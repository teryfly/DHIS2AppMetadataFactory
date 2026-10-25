import { config } from '@/config/runtime';
import { LS, readJSON } from '@/lib/storage';
import { AppError, toApiError } from '@/api/errors';
import { idempotencyKeyFor } from '@/api/idempotency';

type Json = Record<string, unknown> | unknown[];

export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined | null | Array<string | number>>;
  body?: unknown;
  /** Extra headers, e.g. { 'Idempotency-Key': key }. */
  header?: Record<string, string>;
  /** Set false to skip automatic X-Operator / Idempotency injection. */
  mutating?: boolean;
  /** Idempotency intent (see idempotency.ts). Required for retries to reuse a key. */
  intent?: string;
  signal?: AbortSignal;
  parseAs?: 'json' | 'text';
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export function buildUrl(path: string, query?: RequestOptions['query']): string {
  const base = config.apiBaseUrl || '';
  const url = `${base}${config.apiPathPrefix}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) {
      for (const item of v) params.append(k, String(item));
    } else {
      params.set(k, String(v));
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

export function newCorrelationId(): string {
  const c = globalThis.crypto;
  if (c && 'randomUUID' in c) return c.randomUUID();
  return `cid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function languageTag(): string {
  const stored = readJSON<string>(LS.language, '');
  if (stored === 'en' || stored === 'zh-CN') return stored;
  return 'zh-CN';
}

/**
 * Read a readable (non-httpOnly) cookie. Only the CSRF token is ever read here — the session
 * itself is an httpOnly cookie the browser sends and this app never inspects.
 */
export function readCookie(name: string): string {
  if (typeof document === 'undefined') return '';
  const target = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(';')) {
    const kv = part.trim();
    if (kv.startsWith(target)) return decodeURIComponent(kv.slice(target.length));
  }
  return '';
}

/**
 * `X-Operator` as it may legally cross the wire.
 *
 * The Fetch standard restricts a header value to ISO-8859-1, and a Chinese operator name is the
 * normal case for this product — Chromium rejects the whole request rather than mangle it, which
 * the operator experiences as "无法连接服务器". A name that already fits travels unchanged; anything
 * else goes out percent-encoded and S1 decodes it back (`rest/middleware.py:decode_operator`,
 * CP-API-002). ASCII clients, including the documented `curl -H`, are unaffected either way.
 */
export function operatorHeader(name: string): string {
  const withinLatin1 = [...name].every((character) => (character.codePointAt(0) ?? 0) <= 0xff);
  return withinLatin1 ? name : encodeURIComponent(name);
}

/**
 * Core request helper. Injects Accept-Language, X-Correlation-Id and — for state-changing
 * methods only — X-Operator, Idempotency-Key and the CSRF double-submit token (UM-BR-04,
 * UM-NET-001, 00 文档 6.11 跨切请求头).
 */
export async function request<T = unknown>(
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const isMutating = opts.mutating ?? MUTATING.has(method);
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Accept-Language': languageTag(),
    ...opts.header,
  };
  if (config.traceCorrelation) headers['X-Correlation-Id'] = newCorrelationId();
  if (isMutating) {
    const operator = readJSON<string>(LS.operator, '') || '';
    if (operator) headers['X-Operator'] = operatorHeader(operator);
    if (opts.intent) headers['Idempotency-Key'] = idempotencyKeyFor(opts.intent);
    // S1 answers 403 CSRF_FAILED when a cookie-authenticated change omits this header
    // (factory_control/rest/middleware.py). Harmless when the session is bearer-authenticated.
    const csrf = readCookie(config.csrfCookieName);
    if (csrf) headers[config.csrfHeader] = csrf;
  }

  // credentials stays at the fetch default ('same-origin'): S5-M is deployed behind the same
  // origin as S1 (UM-COMP-004) and the dev server proxies /api/v1. Setting 'include' here would
  // break a SameSite=Strict session cookie.
  const init: RequestInit = { method, headers, signal: opts.signal };
  if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), init);
  } catch (e) {
    throw AppError.network(String((e as Error)?.message ?? ''));
  }

  if (!res.ok) {
    let parsed: unknown = undefined;
    try {
      parsed = await res.clone().json();
    } catch {
      try {
        parsed = { message: await res.text() };
      } catch {
        parsed = undefined;
      }
    }
    throw toApiError(parsed as Json, res.status, headersOf(res));
  }

  if (res.status === 204) return undefined as T;
  if (opts.parseAs === 'text') return (await res.text()) as unknown as T;
  const ct = res.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

function headersOf(res: Response): Record<string, string> {
  const out: Record<string, string> = {};
  res.headers.forEach((v, k) => {
    out[k] = v;
  });
  return out;
}

/** Convenience GET. */
export const apiGet = <T>(path: string, opts: RequestOptions = {}) => request<T>('GET', path, opts);
/** Convenience mutating request with an idempotency intent. */
export const apiSend = <T>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  intent?: string,
  opts: RequestOptions = {},
) => request<T>(method, path, { ...opts, body, intent });

/** Build an idempotency-keyed header for manual (e.g. upload) calls. */
export function idemHeader(intent: string): Record<string, string> {
  return { 'Idempotency-Key': idempotencyKeyFor(intent) };
}
