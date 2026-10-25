export interface FactoryConfig {
  /** Absolute S1 base URL. Empty => same-origin (window.location.origin). */
  apiBaseUrl: string;
  /** Absolute base URL for the SSE stream; falls back to apiBaseUrl then origin. */
  sseBaseUrl: string;
  /** API path prefix. Default '/api/v1'. */
  apiPathPrefix: string;
  /** Attach X-Correlation-Id to every request. */
  traceCorrelation: boolean;
  /**
   * S1 reads a CSRF token from this cookie and requires it echoed on a cookie-authenticated
   * state-changing request (S1 config.csrf_cookie_name). Deployment-injectable.
   */
  csrfCookieName: string;
  /** Header the CSRF token is echoed in (S1 config.csrf_header). */
  csrfHeader: string;
}

const build = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '',
  apiPathPrefix: import.meta.env.VITE_API_PATH_PREFIX ?? '/api/v1',
};

const runtime = (globalThis as { __FACTORY_CONFIG__?: Partial<FactoryConfig> }).__FACTORY_CONFIG__ ?? {};

const config: FactoryConfig = {
  apiBaseUrl: stripTrailing(
    runtime.apiBaseUrl !== undefined ? runtime.apiBaseUrl : build.apiBaseUrl,
  ),
  sseBaseUrl: stripTrailing(runtime.sseBaseUrl ?? ''),
  apiPathPrefix: normalizePrefix(runtime.apiPathPrefix ?? build.apiPathPrefix),
  traceCorrelation: runtime.traceCorrelation ?? true,
  csrfCookieName: runtime.csrfCookieName ?? 'factory_csrf',
  csrfHeader: runtime.csrfHeader ?? 'X-CSRF-Token',
};

function stripTrailing(v: string): string {
  return v.replace(/\/+$/, '');
}

function normalizePrefix(v: string): string {
  const p = v.startsWith('/') ? v : `/${v}`;
  return stripTrailing(p);
}

/** Base for REST calls. Empty string => same-origin, browser resolves against current origin. */
export function openapiBaseUrl(): string {
  return config.apiBaseUrl || (typeof window !== 'undefined' ? window.location.origin : '');
}

/** Absolute or same-origin base for the SSE stream. */
export function sseBase(): string {
  return config.sseBaseUrl || config.apiBaseUrl || (typeof window !== 'undefined' ? window.location.origin : '');
}

export function apiPrefix(): string {
  return config.apiPathPrefix;
}

/** SSE URL for a workflow, optionally resuming after a sequence number. */
export function eventsUrl(workflowId: string, after?: number): string {
  const qs = after && after > 0 ? `?after=${after}` : '';
  return `${sseBase()}${config.apiPathPrefix}/workflows/${encodeURIComponent(workflowId)}/events${qs}`;
}

export { config };
