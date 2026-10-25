import { defineConfig, devices } from '@playwright/test';

/** The two whole-page sweeps: each screen is measured once, in one project, not once per viewport. */
const SWEEPS = /a11y\.spec\.ts|tapTarget\.spec\.ts/;

// Playwright drives two things the unit runner cannot: capability measurements that need real
// layout (UM-AC-17 editor, UM-AC-18 graph fps) and the end-to-end click budgets of §6.1, which are
// only meaningful in both a phone and a desktop viewport (UM-NFR-06).
//
// `webServer` boots the hermetic S1 mock (UM-NFR-07) and a preview of the built app, so a run is
// reproducible without a live S1 or DHIS2.
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'tests/results.json' }]],
  timeout: 60_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'off',
    video: 'off',
  },
  projects: [
    // iPhone-class viewport, emulated in Chromium: the CDP touch and CPU-throttling primitives the
    // UM-CAP-002 measurement needs are Chromium-only, and WebKit is not installed on this host.
    // The four measurement projects skip the two full-screen sweeps: axe asks a WCAG question and
    // the tap-target audit a geometry one, and neither needs running four times for the same answer.
    { name: 'mobile-highend', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } }, testIgnore: SWEEPS },
    // "中端手机" for UM-AC-18: a real mid-tier phone is roughly a 4x CPU-throttled Pixel-class
    // device; 6x is recorded as well because the in-app browsers on cheap Android are worse.
    { name: 'mobile-mid', use: { ...devices['Pixel 5'], viewport: { width: 393, height: 760 } }, testIgnore: SWEEPS },
    { name: 'mobile-minwidth', use: { viewport: { width: 360, height: 640 }, deviceScaleFactor: 2 }, testIgnore: SWEEPS },
    { name: 'desktop', use: { viewport: { width: 1280, height: 900 } }, testIgnore: SWEEPS },
    // UM-NFR-05: the automated WCAG 2.1 AA sweep, serialized as its own project (same rule as the
    // desktop console's `a11y` project).
    { name: 'a11y', use: { viewport: { width: 393, height: 844 }, deviceScaleFactor: 2 }, testMatch: /a11y\.spec\.ts/ },
    // UM-UX-001: the 44 × 44 measurement, taken at the narrowest supported viewport (360), which is
    // where a row of controls gets squeezed first and a target drops under the floor.
    { name: 'tap-target', use: { viewport: { width: 360, height: 640 }, deviceScaleFactor: 2 }, testMatch: /tapTarget\.spec\.ts/ },
  ],
  webServer: [
    {
      command: 'node scripts/mock-s1.mjs',
      url: 'http://127.0.0.1:8787/api/v1/projects',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      // Serves the harness build (`npm run build:harness`, run by the npm scripts so a cold build
      // cannot time out this wait). A production build, not the dev server: fps and chunk-shape
      // numbers must describe what a phone would actually download. The dev-server proxy is
      // mirrored for preview in vite.config.ts, so the app stays same-origin here too.
      command: 'VITE_S1_PROXY=http://127.0.0.1:8787 npm run preview -- --outDir dist-e2e --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
