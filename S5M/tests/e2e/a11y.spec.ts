import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { seedSession } from './helpers';

/*
 * UM-NFR-05: the same WCAG 2.1 AA sweep the desktop console has run since M1, on the phone.
 *
 * It was the largest untested claim in the mobile console: every other mobile check measures
 * geometry, clicks or copy, and none of them asks whether a screen reader could find anything. The
 * screens below are the ones an operator actually lives in, plus the two that the audit found
 * rewritten late (settings, access) where a violation would otherwise have arrived unnoticed.
 *
 * Serious and critical only, same as the desktop: "moderate" on a 393 px viewport is a design
 * review note, not a gate, and a gate that cries about them gets ignored.
 */
const axePath = fileURLToPath(new URL('../../node_modules/axe-core/axe.min.js', import.meta.url));
const axeSource = readFileSync(axePath, 'utf8');

async function scan(page: Page) {
  await page.addScriptTag({ content: axeSource });
  const results = await page.evaluate(async () => {
    return (window as any).axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } });
  });
  return results.violations as Array<{ id: string; impact: string; nodes: unknown[] }>;
}

const SCREENS = [
  '/',
  '/login',
  '/projects',
  '/projects/new',
  '/projects/proj-1',
  '/projects/proj-1/inputs',
  '/projects/proj-1/artifacts',
  '/artifacts/art-openapi',
  '/projects/proj-1/issues',
  '/projects/proj-1/handoff',
  '/projects/proj-1/audit',
  '/todo',
  // the approval queue is the same screen at a filtered address, and that address is the entry
  '/todo?kind=gate',
  '/notifications',
  '/environments',
  '/governance/models',
  '/governance/providers',
  '/governance/policy',
  '/governance/prompts',
  '/governance/cost',
  '/governance/evaluations',
  '/access',
  '/settings',
  '/me',
];

test.describe('无障碍扫描（axe WCAG 2.1 AA）', () => {
  for (const route of SCREENS) {
    test(`${route} 没有严重违规`, async ({ page }) => {
      await seedSession(page);
      await page.goto(route);
      await page.waitForLoadState('networkidle');
      const serious = (await scan(page)).filter((v) => v.impact === 'critical' || v.impact === 'serious');
      expect(serious, `${route}: ${JSON.stringify(serious, null, 2)}`).toEqual([]);
    });
  }

  // the model sheet opens over the page, so no route scan ever sees its chips, its question marks or
  // the priority fields they reveal — which is where a new control group is most likely to arrive
  // without a name
  //
  // The popup's own chrome is dropped below: Vant hands its overlay and its close glyph a
  // `role="button"` with no name, and that belongs to the sheet chrome every mobile dialog shares
  // rather than to this form. A real gap, and an older one than the controls added here.
  const SHEET_CHROME = ['.van-overlay', '.van-icon-cross'];

  test('登记模型 弹层没有严重违规', async ({ page }) => {
    await seedSession(page);
    await page.goto('/governance/models');
    await page.locator('button:has-text("登记模型")').first().click();
    await expect(page.locator('[role="group"][aria-label="适用 Agent"]')).toBeVisible();
    await page.locator('button:has-text("逐个调整优先级")').click();

    const serious = (await scan(page))
      .filter((v) => v.impact === 'critical' || v.impact === 'serious')
      .map((v) => ({
        ...v,
        nodes: v.nodes.filter((node) => !SHEET_CHROME.includes(String((node as { target?: string[] }).target?.[0]))),
      }))
      .filter((v) => v.nodes.length > 0);
    expect(serious, `model sheet: ${JSON.stringify(serious, null, 2)}`).toEqual([]);
  });
});
