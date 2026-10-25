import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// UI-PRJ-004 pairs 导出 with 导入. The mobile settings screen had the export button and no import,
// so an operator who exported on the phone could only ever restore at a desk — and the harness had
// no `POST /projects/import` either, which is how a missing half survives its own tests.
const CONTROL = 'http://127.0.0.1:8787/api/v1/_control';

test.describe('项目导入（UI-PRJ-004）', () => {
  test.beforeEach(async ({ request }) => {
    await request.get(`${CONTROL}?mode=imports-reset`);
  });
  test.afterEach(async ({ request }) => {
    await request.get(`${CONTROL}?mode=imports-reset`);
  });

  test('填一个导出产物 ID 就能受理，新项目出现在列表里', async ({ page }) => {
    await seedSession(page);
    await page.goto('/projects/proj-1/settings');
    await page.getByRole('button', { name: '导入' }).first().click();

    const sheet = page.locator('.fui-sheet');
    await expect(sheet.getByText('导入会新建一个项目')).toBeVisible();
    const confirm = sheet.getByRole('button', { name: '导入' });
    await expect(confirm).toBeDisabled();

    await sheet.getByPlaceholder('art-…').fill('art-exp-1');
    await expect(confirm).toBeEnabled();
    await confirm.click();

    await expect(page.getByText(/导入已受理.*art-exp-1|导入已受理/)).toBeVisible();

    await page.goto('/projects');
    await expect(page.getByText('导入的 art-exp-1')).toBeVisible();
  });
});
