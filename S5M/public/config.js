// Runtime configuration — served as a plain script so the SAME static image can point at
// any S1 without a rebuild. Loaded by src/config/runtime.ts via window.__FACTORY_CONFIG__.
// S5M is deployed same-origin with /api/v1 behind one reverse proxy (UM-COMP-004), so these
// can all be left empty for a same-origin deployment.
window.__FACTORY_CONFIG__ = {
  // Empty => same origin (window.location.origin). Set absolute only when S1 is on another host.
  apiBaseUrl: '',
  sseBaseUrl: '',
  apiPathPrefix: '/api/v1',
  // Attach an X-Correlation-Id to every request for troubleshooting (UM-NET-002).
  traceCorrelation: true,
};
