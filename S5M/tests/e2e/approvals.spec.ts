import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

/*
 * UM-NAV-006：手机上的审批队列是「待办中心」里那一句筛选，但筛选属于地址——侧栏那条「待审批」给的
 * 是一个能被收藏、能发给同事的 URL，而不是第二个列表页。
 */

test.describe('待审批的地址（UM-NAV-006）', () => {
  test('带着 kind 打开，就只列等人的审批节点', async ({ page }) => {
    await seedSession(page);
    await page.goto('/todo?kind=gate');
    await expect(page.locator('text=待审批 · 需求整理')).toBeVisible({ timeout: 15_000 });
    // 阻断中的阶段与待人工核实都不在：这一句筛选是地址说了算的
    await expect(page.locator('text=阻断中的阶段 ·')).toHaveCount(0);
    // 节点名读成它关掉的那段活，不是线上记号
    await expect(page.locator('text=REQUIREMENT_GATE')).toHaveCount(0);
  });

  test('在屏幕上换筛选，地址跟着变', async ({ page }) => {
    await seedSession(page);
    await page.goto('/todo');
    await page.locator('button:has-text("待审批")').last().click();
    await expect(page).toHaveURL(/kind=gate/);
    await page.locator('button:has-text("全部")').click();
    await expect(page).not.toHaveURL(/kind=/);
  });

  // 侧栏只在 ≥1024px 出现，所以这一条属于 `desktop` 那一个 project；其余三条在每个视口都该成立
  test('宽屏的侧栏给这一条一个名字', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'desktop', 'the side nav only exists at ≥1024px');
    await seedSession(page);
    await page.goto('/');
    await page.locator('.shell__side-item', { hasText: '待审批' }).click();
    await expect(page).toHaveURL(/\/todo\?kind=gate/, { timeout: 15_000 });
  });

  test('治理的分节标题说的是当前这一节', async ({ page }) => {
    await seedSession(page);
    await page.goto('/governance/providers');
    await expect(page.locator('.fui-topbar, header').first()).toContainText('治理 · 提供方');
    // 不认识的分节回落到第一节，标题跟着一起回落：顶栏不能报一个页面上没有的名字
    await page.goto('/governance/nonsense');
    await expect(page.locator('.fui-topbar, header').first()).toContainText('治理 · 模型');
  });
});
