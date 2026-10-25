import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { DESIGN_PROJECT_ID } from '../../src/mocks/db';

/*
 * UI-NFR-03 / UI-APP-003: automated WCAG 2.1 AA checks run in CI over the key screens via axe-core.
 * Serialized to the `a11y` Playwright project (see playwright.config.ts).
 */
const axePath = fileURLToPath(new URL('../../node_modules/axe-core/axe.min.js', import.meta.url));
const axeSource = readFileSync(axePath, 'utf8');

/** The fixture environment the detail page is scanned for (see `src/mocks/db.ts`). */
const ENVIRONMENT_KEY = 'env-dev';

async function injectAndScan(page: Page) {
  await page.addScriptTag({ content: axeSource });
  const results = await page.evaluate(async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (window as any).axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } });
  });
  return results.violations as Array<{ id: string; impact: string; nodes: unknown[] }>;
}

test.describe('accessibility (axe WCAG 2.1 AA)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    const input = page.getByLabel(/Operator|操作者/);
    await input.fill('A11y Bot');
    await page.getByRole('button', { name: /Start|开始|Save|保存/ }).click();
  });

  // The Administration pages are scanned directly (they need no project); the workspace pages are
  // reached by clicking, because the project id belongs to the fixture and not to the test.
  // `/login` used to be missing from this loop, so the one screen every operator passes through
  // had never been scanned; the environment detail page is new and is scanned by key.
  for (const route of ['/', '/login', '/getting-started', '/projects', '/notifications', '/settings', '/environments', `/environments/${ENVIRONMENT_KEY}`, '/approvals', '/governance/models', '/governance/providers', '/governance/policy', '/governance/prompts', '/governance/cost', '/users', '/roles']) {
    test(`no serious violations on ${route}`, async ({ page }) => {
      await page.goto(route);
      const violations = await injectAndScan(page);
      const serious = violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
    });
  }

  // The registration modal is where the new control group lives: the agent chips, the question marks
  // and the per-agent priority boxes only exist after "Register a model" is pressed, so no route
  // scan above has ever seen them.
  test('no serious violations in the model registration modal', async ({ page }) => {
    await page.goto('/governance/models');
    await page.getByRole('button', { name: /Register a model/i }).click();
    await expect(page.getByRole('group', { name: /Applicable agents/i })).toBeVisible();
    await page.getByRole('button', { name: /Set each priority/i }).click();

    const serious = (await injectAndScan(page)).filter((v) => v.impact === 'critical' || v.impact === 'serious');
    expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
  });

  // One test per section: a single loop over 18 screens used to share one 30 s timeout, and adding
  // two screens (the artifact address and the handoff package) turned a passing sweep into a timeout
  // that looked like an accessibility failure. Per-screen tests also name the screen that broke.
  const SECTIONS = ['overview', 'stages/ST-req', 'inputs', 'metadata', 'decisions', 'graph', 'artifacts', 'artifacts/ART-PKG-1', 'issues', 'deployments', 'change-requests', 'audit', 'members', 'grants', 'exemptions', 'api', 'api/doc', 'handoff', 'settings'];
  for (const section of SECTIONS) {
    test(`no serious violations on /projects/:id/${section}`, async ({ page }) => {
      // The mock backend is what the e2e build runs against (VITE_ENABLE_MOCK=1), so the fixture
      // project is addressed directly: the point is to scan every section, not to re-test routing.
      await page.goto(`/projects/${DESIGN_PROJECT_ID}/${section}`);
      await page.waitForLoadState('networkidle');
      const violations = await injectAndScan(page);
      const serious = violations.filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious, `${section}: ${JSON.stringify(serious, null, 2)}`).toEqual([]);
    });
  }

  // The development package only exists for the mode-B project, so the Handoff section is scanned
  // twice on purpose: once on the project that has one, and once on the mode-A project whose honest
  // answer is the NOT_GENERATED guidance (CP-UI-022). Both are screens an operator sees.
  test('no serious violations on the mode-B handoff package', async ({ page }) => {
    await page.goto('/projects/PRJ-brown-02/handoff');
    await page.waitForLoadState('networkidle');
    const serious = (await injectAndScan(page)).filter((v) => v.impact === 'critical' || v.impact === 'serious');
    expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
  });
});
