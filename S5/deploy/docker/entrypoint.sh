#!/bin/sh
# Regenerate the runtime config.js from container env so one image serves any target (UI-APP-005).
set -e
ROOT=/usr/share/nginx/html
cat > "$ROOT/config.js" <<EOF
window.__FACTORY_CONFIG__ = {
  apiBaseUrl: "${FACTORY_API_BASE_URL:-}",
  sseBaseUrl: "${FACTORY_SSE_BASE_URL:-}",
  apiPathPrefix: "${FACTORY_API_PATH_PREFIX:-/api/v1}",
  enableMock: ${FACTORY_ENABLE_MOCK:-false},
  traceCorrelation: ${FACTORY_TRACE_CORRELATION:-true}
};
EOF
exec "$@"
