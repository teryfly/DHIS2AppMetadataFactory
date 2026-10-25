/*
 * Runtime configuration (UI-APP-005): read from window.__FACTORY_CONFIG__ which is served as
 * a static file (/config.js) before the bundle, so one image can target a different S1 per
 * environment. Falls back to same-origin /api/v1 (UI-APP-008).
 */

export interface RuntimeConfig {
  /** Base URL of S1 REST. '' → same origin. Never includes the /api/v1 prefix. */
  apiBaseUrl: string;
  /** Base URL for the SSE stream. '' → derived from apiBaseUrl. */
  sseBaseUrl: string;
  /** REST path prefix served by S1. */
  apiPathPrefix: string;
  /** Enable the in-browser MSW mock layer. */
  enableMock: boolean;
  /** Propagate X-Correlation-Id / traceparent. */
  traceCorrelation: boolean;
}

interface GlobalWindow extends Window {
  __FACTORY_CONFIG__?: Partial<RuntimeConfig>;
}

const DEFAULTS: RuntimeConfig = {
  apiBaseUrl: '',
  sseBaseUrl: '',
  apiPathPrefix: '/api/v1',
  enableMock: false,
  traceCorrelation: true,
};

// import.meta.env is populated at build time; runtime window values take precedence so the
// same artifact stays portable (UI-APP-005). VITE_* are only dev/build conveniences.
function buildFromEnv(): RuntimeConfig {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  return {
    ...DEFAULTS,
    apiBaseUrl: env.VITE_API_BASE_URL ?? DEFAULTS.apiBaseUrl,
    apiPathPrefix: env.VITE_API_PATH_PREFIX ?? DEFAULTS.apiPathPrefix,
    enableMock: env.VITE_ENABLE_MOCK === '1' || env.VITE_ENABLE_MOCK === 'true',
  };
}

let cached: RuntimeConfig | null = null;

export function loadRuntimeConfig(): RuntimeConfig {
  if (cached) return cached;
  cached = mergeConfig(buildFromEnv(), (globalThis as unknown as { window?: GlobalWindow }).window?.__FACTORY_CONFIG__ ?? {});
  return cached;
}

/**
 * The served config wins for everything it says, with one exception: `enableMock` is a property of
 * the bundle (the mock code is either in it or not), so a deployment file cannot switch a
 * build-time mock off. That exception is what made the e2e "mock build" send its API calls to a
 * server that was not there.
 */
export function mergeConfig(build: RuntimeConfig, injected: Partial<RuntimeConfig>): RuntimeConfig {
  const merged: RuntimeConfig = { ...build, ...stripUndefined(injected) };
  if (build.enableMock) merged.enableMock = true;
  return merged;
}

/** Reset for tests only. */
export function __resetRuntimeConfigForTest(next?: Partial<RuntimeConfig>): void {
  cached = next ? { ...DEFAULTS, ...next } : null;
}

function stripUndefined<T extends object>(obj: T): Partial<T> {
  const out: Partial<T> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) (out as Record<string, unknown>)[k] = v;
  }
  return out;
}

/** Absolute-ish URL for a REST path (already starting with `/api/v1/...` or a resource path). */
export function restUrl(pathOrResource: string): string {
  const cfg = loadRuntimeConfig();
  const resource = pathOrResource.startsWith('/') ? pathOrResource : `${cfg.apiPathPrefix}/${pathOrResource}`;
  const base = cfg.apiBaseUrl.replace(/\/+$/, '');
  // openapi-fetch supplies the prefix itself; this helper is for raw fetches (upload, SSE).
  return `${base}${resource.startsWith(cfg.apiPathPrefix) ? resource : cfg.apiPathPrefix + resource}`;
}

/** Base URL passed to openapi-fetch (prefix is included here so `client.GET('/projects')` works). */
export function apiBase(): string {
  const cfg = loadRuntimeConfig();
  return `${cfg.apiBaseUrl.replace(/\/+$/, '')}${cfg.apiPathPrefix}`;
}

/**
 * openapi-fetch baseUrl. The generated `paths` keys already begin with the `/api/v1` prefix
 * (that is how S1 declares them), so the client baseUrl must NOT repeat the prefix. Empty string
 * keeps requests same-origin (UI-APP-008). When no base is configured we resolve to the current
 * origin (needed so absolute-URL fetches work under Node/jsdom test environments too).
 */
export function openapiBaseUrl(): string {
  const base = loadRuntimeConfig().apiBaseUrl.replace(/\/+$/, '');
  if (base) return base;
  const origin = (globalThis as unknown as { window?: { location?: { origin?: string } } }).window?.location?.origin;
  return origin ?? '';
}

/** SSE URL for a workflow's event stream, with optional replay-after sequence. */
export function eventsUrl(workflowId: string, after?: number): string {
  const cfg = loadRuntimeConfig();
  const base = (cfg.sseBaseUrl || cfg.apiBaseUrl).replace(/\/+$/, '');
  const q = after != null ? `?after=${after}` : '';
  return `${base}${cfg.apiPathPrefix}/workflows/${encodeURIComponent(workflowId)}/events${q}`;
}
