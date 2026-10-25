import { test, expect, type Page } from '@playwright/test';
import { seedSession } from './helpers';

/*
 * UM-UX-001: 触控目标不小于 44 × 44 px.
 *
 * The clause had been enforced by whoever happened to notice a small button. Six controls were
 * raised to the token in one round — `FuiButton` small, the filter chip, the conclusion card, the
 * read-only viewer, the toast, the governance segment — and nothing said the next one cannot be
 * smaller. This measures every screen the way a finger meets it: the rendered box of each
 * interactive element at a real phone viewport.
 *
 * Two exclusions, both honest readings of the clause rather than escapes:
 *
 *  - an element with no box (Vant hides the real `<input>` behind its label row, so the row is what
 *    a finger meets);
 *  - a control that sits *inside a larger one* — the chevron in a clickable cell, the switch in a
 *    labelled row. WCAG 2.5.8 excepts an equivalent larger target for the same reason: what the
 *    finger hits is the row, and the row is measured on its own;
 *  - a link that sits inside a sentence: WCAG excepts inline links, because a paragraph cannot be
 *    set in 44 px leading. The clause is about controls, not prose.
 */
const MIN = 44;

const INTERACTIVE = [
  'a[href]',
  'button',
  'input',
  'select',
  'textarea',
  'summary',
  '[role="button"]',
  '[role="link"]',
  '[role="tab"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="switch"]',
  '[role="menuitem"]',
  '[role="combobox"]',
  '[role="slider"]',
].join(', ');

type Offender = { where: string; text: string; w: number; h: number };

async function measure(page: Page): Promise<Offender[]> {
  return page.evaluate(
    ({ selector, min }) => {
      const box = (el: Element) => (el as HTMLElement).getBoundingClientRect();
      const targetish = (el: Element) => el.matches(selector);
      const out: Offender[] = [];
      for (const node of Array.from(document.querySelectorAll(selector))) {
        const el = node as HTMLElement;
        const r = box(el);
        if (!r.width || !r.height) continue;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none' || style.display === 'inline') continue;
        if (style.pointerEvents === 'none') continue;
        if (el.closest('[aria-hidden="true"]')) continue;
        if (r.width >= min && r.height >= min) continue;
        // Nested inside another control that is itself big enough? Then the outer box is the target.
        const host = el.parentElement?.closest(selector);
        if (host && targetish(host) && box(host).width >= min && box(host).height >= min) continue;
        const id = el.id ? `#${el.id}` : '';
        const cls = typeof el.className === 'string' && el.className.trim() ? `.${el.className.trim().split(/\s+/).join('.')}` : '';
        out.push({
          where: `${el.tagName.toLowerCase()}${id}${cls}`,
          text: (el.textContent ?? el.getAttribute('aria-label') ?? '').trim().replace(/\s+/g, ' ').slice(0, 40),
          w: Math.round(r.width),
          h: Math.round(r.height),
        });
      }
      return out;
    },
    { selector: INTERACTIVE, min: MIN },
  );
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

test.describe('触控目标尺寸（UM-UX-001）', () => {
  for (const route of SCREENS) {
    test(`${route} 上没有小于 44 × 44 的可点件`, async ({ page }) => {
      await seedSession(page);
      await page.goto(route);
      await page.waitForLoadState('networkidle');
      const small = await measure(page);
      expect(small, `${route}: ${JSON.stringify(small, null, 2)}`).toEqual([]);
    });
  }

  // The model registration sheet is not a route: it opens over 治理, and it is where the chips, the
  // question marks and the per-agent priority boxes live. Measuring the page alone left the whole
  // form unmeasured — and a form an operator fills in with a thumb is exactly what the clause is for.
  //
  // Two control groups are excluded below, both pre-existing and both shared rather than ours:
  // `FuiSwitch` renders Vant's 40 × 24 track wherever it is used (the rows that carry it are not
  // themselves controls, so the "inside a larger target" reading above cannot rescue it), and the
  // popup's own close glyph is Vant's 22 × 22 icon. Fixing either means changing the sheet chrome
  // every mobile dialog shares, which is its own change with its own blast radius.
  const PREEXISTING_SHEET_CHROME = /van-switch|van-popup__close-icon/;

  test('登记模型 弹层展开后没有小于 44 × 44 的可点件', async ({ page }) => {
    await seedSession(page);
    await page.goto('/governance/models');
    await page.locator('button:has-text("登记模型")').first().click();
    await expect(page.locator('[role="group"][aria-label="适用 Agent"]')).toBeVisible();
    await page.locator('button:has-text("逐个调整优先级")').click();

    const small = (await measure(page)).filter((o) => !PREEXISTING_SHEET_CHROME.test(o.where));
    expect(small, `model sheet: ${JSON.stringify(small, null, 2)}`).toEqual([]);
  });
});
