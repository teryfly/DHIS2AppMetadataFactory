import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

// S1 API base used only for the dev-server proxy so /api/v1 stays same-origin in dev
// (UI-APP-008). In production the reverse proxy fronts both static assets and /api/v1.
const S1_PROXY_TARGET = process.env.VITE_S1_PROXY ?? 'http://localhost:8000';

// S1's session cookie is flagged Secure (FR-SEC-005), so a browser only keeps it on an HTTPS
// origin. Give the dev server a certificate through these two variables to sign in during
// development; unset leaves plain http://localhost as it was.
const DEV_TLS_KEY = process.env.VITE_DEV_TLS_KEY;
const DEV_TLS_CERT = process.env.VITE_DEV_TLS_CERT;

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    https:
      DEV_TLS_KEY && DEV_TLS_CERT
        ? { key: readFileSync(DEV_TLS_KEY), cert: readFileSync(DEV_TLS_CERT) }
        : undefined,
    allowedHosts: process.env.VITE_ALLOWED_HOSTS ? process.env.VITE_ALLOWED_HOSTS.split(',') : undefined,
    // Only proxy when not using the in-browser mock so requests hit a real S1.
    proxy: process.env.VITE_ENABLE_MOCK
      ? undefined
      : {
          '/api/v1': {
            target: S1_PROXY_TARGET,
            changeOrigin: true,
            // SSE must not be buffered by the proxy.
            configure: (proxy) => {
              proxy.on('proxyReq', (proxyReq) => proxyReq.setHeader('X-Forwarded-Proto', 'http'));
            },
          },
        },
  },
  build: {
    sourcemap: false,
    target: 'es2022',
    // Monaco + dhis2 are large and shared by nearly every screen, so they are worth a stable
    // chunk of their own. The dependency graph (React Flow) is deliberately NOT listed: naming it
    // here hoisted it into the entry's static import graph, which put 57 KB of gzip on every first
    // paint (UI-APP-010) even though only the graph route needs it. It now stays inside the lazy
    // chunk that imports it.
    rollupOptions: {
      output: {
        // Keep React in its own chunk. Listing `@dhis2/ui` as a whole chunk used to pull React
        // into that chunk too, so the shell imported it on first paint even when no entry screen
        // used a dhis2 control. The dhis2 chunk is now reached only through lazy pages.
        manualChunks(id) {
          if (id.includes('node_modules/@monaco-editor/') || id.includes('node_modules/monaco-editor/')) {
            return 'monaco';
          }
          if (id.includes('node_modules/@dhis2/')) return 'dhis2';
          if (
            id.includes('node_modules/react/') ||
            id.includes('node_modules/react-dom/') ||
            id.includes('node_modules/scheduler/')
          ) {
            return 'react';
          }
          return undefined;
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    // Testing Library waits up to 8s for async UI; the case timeout must not cut that short.
    testTimeout: 15000,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    exclude: ['tests/e2e/**', 'node_modules/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      reportsDirectory: './coverage',
      // Only exclude non-testable glue: bootstrap, generated code, fixtures, config.
      exclude: [
        'src/main.tsx',
        'src/api/generated/**',
        'src/mocks/**',
        'src/test/**',
        'tests/**',
        'dist/**',
        'dist-e2e/**',
        'public/**',
        'coverage/**',
        '**/*.test.{ts,tsx}',
        '**/*.config.{ts,js}',
        'src/vite-env.d.ts',
        'src/**/*.d.ts',
        'src/lib/monacoSetup.ts',
        // Type-only modules: `export type` produces no runtime code, so they can't be "covered".
        'src/api/types.ts',
        'src/features/workspace/WorkspaceContext.ts',
        'scripts/**',
      ],
      thresholds: {
        statements: 80,
        branches: 75,
        functions: 80,
        lines: 80,
      },
    },
  },
});
