/*
 * Runtime configuration (UI-APP-005). This file is served as a static asset and is NOT
 * bundled, so the same image can be deployed against a different S1 backend by replacing
 * it. It must be loaded before the app bundle (see index.html).
 *
 * The reverse proxy keeps static assets and /api/v1 same-origin (UI-APP-008), so the
 * defaults below (empty baseUrl) mean "call /api/v1 on the current origin".
 */
window.__FACTORY_CONFIG__ = {
  // Base URL of the S1 public REST API. Empty string => same origin, path prefix /api/v1.
  apiBaseUrl: '',
  // Base URL of the SSE event stream. Empty => derived from apiBaseUrl.
  sseBaseUrl: '',
  // Path prefix under which S1 REST lives. Rarely changed.
  apiPathPrefix: '/api/v1',
  // Enable the built-in MSW mock layer without a backend. Overridable at build via VITE_ENABLE_MOCK.
  enableMock: false,
  // Correlation id / trace propagation is on by default; disable only for debugging.
  traceCorrelation: true,
};
