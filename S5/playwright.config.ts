import { defineConfig, devices } from '@playwright/test';

// UI-NFR-04: end-to-end on the main path (create project → stages → Gate → VALIDATE → COMMIT →
// post-verify), against the MSW mock build so the suite is hermetic and needs no live S1.
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
    viewport: { width: 1280, height: 900 }, // UI-APP-004: minimum viewport width 1280
  },
  projects: [
    // Both projects were the same Desktop Chrome with no `testMatch`, so every one of the 42 tests ran
    // twice and "84 passed" described 42. The axe sweep is its own project for the same reason the
    // mobile console split its two full-page sweeps out: it asks a different question, and running it
    // a second time at the same viewport answers nothing new.
    { name: 'chromium', use: { ...devices['Desktop Chrome'] }, testIgnore: /a11y\.spec\.ts/ },
    { name: 'a11y', use: { ...devices['Desktop Chrome'] }, testMatch: /a11y\.spec\.ts/ },
  ],
  webServer: {
    // The mock is baked in at build time, so this build gets its own directory: reusing `dist/`
    // is how a deployed console ends up answering from fixture data after an e2e run.
    command: 'npm run build:e2e && npm run preview -- --outDir dist-e2e --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
