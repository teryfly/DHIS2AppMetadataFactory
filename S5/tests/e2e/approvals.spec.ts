import { test, expect, type Page } from '@playwright/test';

/*
 * The approver's two screens, end to end on the mock build (no live S1):
 *   sidebar → /approvals → a row → the gate itself, and Registry → 决策 → /projects/:id/decisions.
 *
 * Both exist because something on screen looked like a way in and was not one: 审批 had no
 * destination at all, and the Registry's 决策 column was a link onto the router's catch-all. A
 * component test can prove a form posts the right body; only a browser proves the address a person
 * types or clicks is served by a page.
 */

async function setOperator(page: Page): Promise<void> {
  await page.getByLabel(/Operator|操作者/).fill('Ana Ruiz');
  await page.getByRole('button', { name: /Start|开始|Save|保存/ }).click();
}

test('the approval queue is one click from the main navigation (UI-APPR-001)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page);

  await page.getByRole('link', { name: /Awaiting approval|待审批/ }).click();
  await expect(page).toHaveURL(/\/approvals$/);

  // one row per gate that is stopped, named by the work it closes rather than by the wire token
  // the project names are also options in the filter, so the row itself is what is being asked for
  await expect(page.getByRole('strong').filter({ hasText: 'Community Health Register' })).toBeVisible();
  await expect(page.getByRole('strong').filter({ hasText: 'ANC Tally Migration' })).toBeVisible();
  await expect(page.getByText('REQUIREMENT_GATE')).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Open to decide|去决定/ })).toHaveCount(2);

  // the count is S1's, and the filter leaves with the request rather than being applied afterwards
  await expect(page.getByText(/2 of 2|已列出 2/)).toBeVisible();
  await page.getByRole('checkbox', { name: /Awaiting my decision|只看等我批的/ }).check();
  await expect(page.getByRole('link', { name: /Open to decide|去决定/ })).toHaveCount(1);
});

test('a row opens the gate that has to be decided (UI-APPR-001, UI-GATE-001)', async ({ page }) => {
  await page.goto('/approvals');
  await setOperator(page);

  const first = page.getByRole('link', { name: /Open to decide|去决定/ }).first();
  const href = await first.getAttribute('href');
  expect(href).toBe('/projects/PRJ-green-01/gate/GT-1');
  await first.click();
  await expect(page).toHaveURL(/\/projects\/PRJ-green-01\/gate\/GT-1$/);
  // six questions (UI-GATE-001), and the section rail still says where the operator is standing
  await expect(page.getByRole('heading', { name: /Why \(decisions & rationale\)|为什么（决策与理由）/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: /What you can change|你可以改变什么/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Design|设计/ })).toHaveAttribute('aria-current', 'page');
});

test('the Registry names a decision and that link is a page (UI-REV-007)', async ({ page }) => {
  await page.goto('/projects/PRJ-green-01/metadata');
  await setOperator(page);

  await page.getByRole('link', { name: 'MAP-017' }).click();
  await expect(page).toHaveURL(/\/projects\/PRJ-green-01\/decisions\?ids=MAP-017$/);
  // the row's own reason, and a way back to the whole list — not the 404 screen this used to reach
  await expect(page.getByText(/org-unit tree|org-unit/)).toBeVisible();
  await expect(page.getByRole('link', { name: /show every decision|查看全部决策/ })).toBeVisible();
  expect(await page.getByRole('heading', { name: '404' }).count()).toBe(0);

  await page.goto('/projects/PRJ-green-01/decisions');
  await expect(page.getByText('ARC-002')).toBeVisible();
});
