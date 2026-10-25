import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// UM-AC-20/21/23: 全新装机时，「开始使用」必须说得出还差哪一步，并且**就在这一屏把它做完**。
// `_control?mode=bare` 把服务端切回「什么都没登记」的样子 —— 有数据在的时候，所有前置条件看起来
// 都是满足的，引导也就没人去按。
//
// 这一轮真正要守住的是一条新承诺：凭证的值可以只在浏览器里存在一瞬。所以除了步骤条，
// 这里还要证明录进去的值没有留在浏览器里 —— 那是界面自己说了算的事，服务端测不出来。

const mockControl = 'http://127.0.0.1:8787/api/v1/_control';
const SECRET = 'Playwright-Only-Tok3n';

test.describe('UM-AC-20 装机引导（冷启动，一步一屏）', () => {
  test.beforeEach(async ({ request }) => {
    // bare 把 `/setup` 换成「什么都没登记」的样子：providers 全 fake、环境 0、模型 0、项目 0。
    // 有数据在的时候每一步看起来都已完成，引导也就没人去按。
    await request.get(`${mockControl}?mode=bare`);
  });
  test.afterEach(async ({ request }) => {
    await request.get(`${mockControl}?mode=configured`);
  });

  test('工作台把未完成的那一步顶在最前面', async ({ page }) => {
    await seedSession(page);
    await page.goto('/');
    await expect(page.locator('text=还没配置完成')).toBeVisible({ timeout: 15_000 });
  });

  test('步骤条列出四步，只展开当前那一步', async ({ page }) => {
    await seedSession(page);
    await page.goto('/start');
    const chips = page.locator('.start__chip');
    await expect(chips).toHaveCount(4);
    // 步骤条给的是「第几步 + 状态」，四步都在，且当前那一步只有一句 —— 这就是"一步一屏"
    await expect(chips).toHaveText([
      /模型提供方与模型/,
      /DHIS2 环境/,
      /第一个项目/,
      /跑阶段、审批、正式提交/,
    ]);
    await expect(page.locator('[aria-current="step"]')).toHaveCount(1);
    // 模型排在最前：没有可用模型时，这套部署上没有任何一步走得下去
    await expect(page.locator('[aria-current="step"]')).toContainText('模型提供方与模型');
    // 替身模式不能被当成「已就绪」
    await expect(page.locator('text=不可交付').first()).toBeVisible({ timeout: 15_000 });
  });

  test('能力接入只作为事实陈述，不布置一个完不成的任务', async ({ page }) => {
    // `GET /setup` 的 providers 是 S2/S3/S4 真身还是替身 —— 由部署自己的 env 文件决定，
    // 浏览器里改不了。它曾经是清单项，现在只在页尾说清楚，不给按钮。
    await seedSession(page);
    await page.goto('/start');
    const card = page.locator('.fui-card').filter({ hasText: '能力接入' });
    await expect(card.locator('text=演示替身').first()).toBeVisible({ timeout: 15_000 });
    await expect(card.locator('text=服务端报告：')).toBeVisible();
    await expect(card.locator('button')).toHaveCount(0);
  });

  test('环境这一步就地打开登记表单，不离开 /start', async ({ page }) => {
    await seedSession(page);
    await page.goto('/start');
    await page.locator('.start__chip', { hasText: 'DHIS2 环境' }).click();
    await page.locator('button:has-text("去登记环境")').first().click();
    await expect(page).toHaveURL(/\/start/);
    await expect(page.locator('text=环境键').first()).toBeVisible({ timeout: 10_000 });
  });

  test('凭证录入：值发出去一次，留下的是引用，浏览器里查不到值（UM-AC-23）', async ({ page }) => {
    await seedSession(page);
    await page.goto('/start');
    await page.locator('.start__chip', { hasText: 'DHIS2 环境' }).click();
    await page.locator('button:has-text("去登记环境")').first().click();
    await expect(page.locator('button:has-text("录入新凭证")').first()).toBeVisible({ timeout: 10_000 });

    await page.locator('button:has-text("录入新凭证")').first().click();
    // 只有这一个 password 输入：值不该以肉眼可读的形式停在任何地方
    const token = page.locator('input[type="password"]').first();
    await expect(token).toBeVisible();
    await token.fill(SECRET);
    await page.locator('button:has-text("加密保存并填入引用")').click();

    // 引用被回填，且响应里本来就没有值可回显（替身与真服务端同一条承诺）。
    // Vant 的 Field 没有 label↔input 的关联，所以按组件自己的根类定位；toHaveValue 读的是
    // property —— Vue 绑定不写 attribute，属性选择器匹配不到。
    await expect(page.locator('.cred input').first()).toHaveValue(/^db:\/\//, { timeout: 10_000 });
    await expect(page.locator('text=已加密保存为').first()).toBeVisible();

    const held = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
    expect(held).not.toContain(SECRET);
    await expect(page.locator('body')).not.toContainText(SECRET);
  });

  test('向导在环境一步给出原因与入口，而不是停在空白卡', async ({ page }) => {
    await seedSession(page);
    await page.goto('/projects/new');
    // 三步向导：第 1 步只有一个必填名称，用表单里的第一个输入框定位（顶部还有搜索框）
    await page.locator('.wiz input').first().fill('冷启动演练');
    await page.locator('button:has-text("下一步")').click();
    await page.locator('button:has-text("下一步")').click();
    await expect(page.locator('text=还没有登记任何 DHIS2 环境')).toBeVisible({ timeout: 15_000 });
    await page.locator('button:has-text("去登记环境")').click();
    await expect(page).toHaveURL(/\/environments/, { timeout: 10_000 });
  });
});

test.describe('UM-AC-21 已配置时不再打扰', () => {
  test.beforeEach(async ({ request }) => {
    await request.get(`${mockControl}?mode=configured`);
  });

  test('全部就绪时工作台不显示引导卡', async ({ page }) => {
    await seedSession(page);
    await page.goto('/');
    await expect(page.locator('text=还没配置完成')).toHaveCount(0);
    // 项目本身仍然列得出来，收起的只是引导
    await expect(page.locator('text=免疫规划试点').first()).toBeVisible({ timeout: 15_000 });
  });
});
