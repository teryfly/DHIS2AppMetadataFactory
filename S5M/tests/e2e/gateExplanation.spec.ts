import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

/*
 * UM-GATE-001 六问里有两问，服务端的答案是**结构体**：`why` 是一批设计决策，`canChange` 是一批可做的动作。
 * 手机上以前把它们 `JSON.stringify` 出来——括号、引号、`null` 全在屏幕上，等于让审批人自己解析返回值。
 * 现在一行一条（`labels.ts` 的 `decisionLine` / `decisionText`，与桌面 `DecisionCard` 同一批条目）。
 * 这几条钉的是「屏幕上不再有 JSON」，不是某个措辞：措辞归语言资源，形状归这里。
 */

test.describe('审批节点的「为什么」与「可以改变什么」（UM-GATE-001）', () => {
  test.beforeEach(async ({ page }) => {
    await seedSession(page);
    await page.goto('/gates/gate-1');
  });

  test('为什么：读成一行决策，屏幕上没有花括号', async ({ page }) => {
    const why = page.locator('.gt__expl-sec').filter({ has: page.locator('b', { hasText: '为什么' }) }).first();
    await expect(why).toBeVisible({ timeout: 15_000 });
    await expect(why).toContainText('用 categoryOption 表达年龄段');
    await expect(why).toContainText('既有统计口径不能改');
    expect(await why.innerText()).not.toMatch(/[{}]/);
    expect(await why.innerText()).not.toContain('"');
  });

  test('可以改变什么：读成动作，而不是带引号的字段名', async ({ page }) => {
    const can = page.locator('.gt__expl-sec').filter({ has: page.locator('b', { hasText: '可以改变什么' }) }).first();
    await expect(can).toBeVisible({ timeout: 15_000 });
    // 动作名本身要原样保留：那是 S1 的 affordance 标识，翻译它就等于给权限造第二个名字
    await expect(can).toContainText('action: stage.rerun');
    await expect(can).toContainText('invalidatesGate: true');
    expect(await can.innerText()).not.toMatch(/[{}]/);
  });

  test('这一屏说的是它关掉哪一段活', async ({ page }) => {
    // 替身里 gate-1 是 REQUIREMENT_GATE；页头要写「审批 · 需求整理」，与桌面 GatePage 的页头同一句话
    const head = page.locator('h2.gt__gate');
    await expect(head).toBeVisible({ timeout: 15_000 });
    await expect(head).toContainText('需求整理');
    expect(await head.innerText()).not.toContain('REQUIREMENT_GATE');
  });

  test('绑定的对象类型读成词，节点类型不读成线上记号', async ({ page }) => {
    const body = await page.locator('body').innerText();
    await expect(page.locator('text=REQUIREMENT_GATE')).toHaveCount(0);
    expect(body).not.toContain('WAITING_APPROVAL');
  });
});
