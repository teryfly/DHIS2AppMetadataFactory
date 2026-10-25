import { defineConfig, type PluginOption, type ProxyOptions } from 'vite';
import vue from '@vitejs/plugin-vue';
import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

// In production the reverse proxy fronts both the static assets and /api/v1 (UM-COMP-004,
// same-origin like S5). The dev-server proxy below keeps /api/v1 same-origin during development
// so the client needs no absolute base URL.
const S1_PROXY_TARGET = process.env.VITE_S1_PROXY ?? 'http://127.0.0.1:8000';
const ENABLE_PWA = process.env.VITE_ENABLE_PWA === '1';

// S1's session cookie is flagged Secure (FR-SEC-005), so a browser only keeps it on an HTTPS
// origin. Give the dev server a certificate through these two variables to sign in during
// development; unset leaves plain http://localhost as it was.
const DEV_TLS_KEY = process.env.VITE_DEV_TLS_KEY;
const DEV_TLS_CERT = process.env.VITE_DEV_TLS_CERT;

// Same-origin API in every mode: dev and preview both proxy /api/v1, so the client never needs an
// absolute base URL and the e2e suite measures the same bundle the browser would download in prod.
const PROXY: Record<string, ProxyOptions> = {
  '/api/v1': {
    target: S1_PROXY_TARGET,
    changeOrigin: true,
    // SSE must not be buffered or compressed by the proxy.
    configure: (proxy) => {
      proxy.on('proxyRes', (proxyRes, _req, res) => {
        if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
          proxyRes.headers['x-accel-buffering'] = 'no';
          (res as { flushHeaders?: () => void }).flushHeaders?.();
        }
      });
    },
  },
};

export default defineConfig(async () => {
  const plugins: PluginOption[] = [vue()];
  if (ENABLE_PWA) {
    const { VitePWA } = await import('vite-plugin-pwa');
    plugins.push(
      VitePWA({
        registerType: 'autoUpdate',
        includeAssets: ['config.js'],
        manifest: {
          name: '元数据工厂 · 移动端',
          short_name: '工厂移动端',
          description: 'DHIS2 元数据工厂 —— 移动优先控制台',
          lang: 'zh-CN',
          theme_color: '#1677ff',
          background_color: '#ffffff',
          display: 'standalone',
          start_url: '/',
          icons: [
            { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
            { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          ],
        },
        workbox: {
          // Only static assets are cached offline (UM-NET-005): business API results are never
          // persisted to durable storage.
          globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
          navigateFallback: 'index.html',
          runtimeCaching: [
            {
              urlPattern: ({ url }) => url.pathname.startsWith('/api/v1'),
              handler: 'NetworkOnly',
            },
          ],
        },
      }),
    );
  }
  return {
    plugins,
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      port: 5174,
      https:
        DEV_TLS_KEY && DEV_TLS_CERT
          ? { key: readFileSync(DEV_TLS_KEY), cert: readFileSync(DEV_TLS_CERT) }
          : undefined,
      allowedHosts: process.env.VITE_ALLOWED_HOSTS ? process.env.VITE_ALLOWED_HOSTS.split(',') : undefined,
      proxy: PROXY,
    },
    // `vite preview` must proxy too, or the e2e suite would have to measure a dev-server build.
    preview: {
      port: 4173,
      proxy: PROXY,
    },
    build: {
      target: 'es2022',
      sourcemap: false,
      // UM-NFR-02: a route chunk over ~150 KB gzip should be a deliberate exception, not a
      // silent regression. The budget gate (scripts/check-bundle-budget.mjs) enforces it.
      chunkSizeWarningLimit: 260,
      rollupOptions: {
        output: {
          // Heavy capabilities (CodeMirror, the graph renderer, the OpenAPI viewer) must NOT be
          // listed here: a manual entry hoists them into a first-screen-adjacent shared chunk and
          // defeats the route-level lazy loading UM-TECH-002 requires.
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia', 'vue-i18n'],
            query: ['@tanstack/vue-query'],
            vant: ['vant'],
          },
        },
      },
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      // FUI components import `vant/es/<x>/style`, which pulls real .css through ESM. Without this,
      // any test that mounts a FUI component dies on "Unknown file extension .css".
      server: { deps: { inline: [/vant/, /\.css$/] } },
    },
  };
});
