import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// UM-AC-19: "基线编译前打开依赖图 → 显示『完成基线编译后可查看』及跳转按钮".
// The mock answers with S1's *current* shape (404 NOT_FOUND with no details.reason) while
// MOCK_UNCOMPILED is on, which is what the transitional mapping in src/api/notGenerated.ts exists
// for. Flipping the control back to "compiled" proves the guidance is conditional, not hardcoded.

test.describe('UM-AC-19 引导式空状态', () => {
  test.beforeEach(async ({ request }) => {
    await request.get('http://127.0.0.1:8787/api/v1/_control?mode=uncompiled');
  });

  test.afterEach(async ({ request }) => {
    await request.get('http://127.0.0.1:8787/api/v1/_control?mode=compiled');
  });

  test('依赖图未编译时给出阶段名与可用跳转', async ({ page }) => {
    await seedSession(page);
    await page.goto(`/projects/proj-1/graph`);
    const guidance = page.locator('text=完成基线编译后可查看');
    await expect(guidance).toBeVisible({ timeout: 15_000 });
    await page.locator('button:has-text("去基线编译")').click();
    await expect(page).toHaveURL(/\/stages\/stage-baseline-compile/, { timeout: 10_000 });
  });

  test('开发包 kind=NONE 同样落到引导态', async ({ page }) => {
    await seedSession(page);
    await page.goto('/projects/proj-1/package');
    await expect(page.locator('text=完成交付后可查看')).toBeVisible({ timeout: 15_000 });
  });

  test('同一页面在已编译数据下不再显示引导', async ({ page }) => {
    await page.request.get('http://127.0.0.1:8787/api/v1/_control?mode=compiled');
    await seedSession(page);
    await page.goto('/projects/proj-1/graph');
    await expect(page.locator('.fui-graph')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('text=完成基线编译后可查看')).toHaveCount(0);
  });
});
