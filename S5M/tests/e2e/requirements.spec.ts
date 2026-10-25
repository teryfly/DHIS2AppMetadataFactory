import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// 需求条目页与提供方连通测试（UM-REQ-003 的手工那一半，与 S5IF-GOV20）。
//
// 写操作在线上的样子由 `requirementsView.test.ts` 钉住（它断言发出去的 RFC 6902 操作列表，
// 那是本界面唯一 S2 能读的形体）；这里守的是另外两件只能在浏览器里看到的事：条目列表真的从
// `blueprint/entries` 渲染出来（而不是那个永远为 null 的 draft.document），以及提供方探测的
// 失败原因真的说得出话——那个按钮存在的意义就是把「地址错」「密钥被拒」「模型太慢」分开。

const mockControl = 'http://127.0.0.1:8787/api/v1/_control';

test.describe('需求条目与提供方连通测试', () => {
  test('需求条目页列出草稿里的条目，并提供手工新增的入口', async ({ page }) => {
    await seedSession(page);
    await page.goto('/projects/proj-1/requirements');
    await expect(page.getByText('每一条接种记录都要能追溯到填报人。')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('新增需求')).toBeVisible();
    // 读模型的位置符不是条目字段：出现 _path 就说明界面把 S2 的地址当成数据渲染了
    expect(await page.locator('body').innerText()).not.toContain('_path');
  });

  test('关闭的阶段不再给出任何写入口，并指向变更请求', async ({ page, request }) => {
    await request.get(`${mockControl}?mode=patch-closed`);
    try {
      await seedSession(page);
      await page.goto('/projects/proj-1/requirements');
      await expect(page.getByText('变更请求')).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText('新增需求')).toHaveCount(0);
    } finally {
      await request.get(`${mockControl}?mode=patch-open`);
    }
  });

  test('提供方「测试连接」把不可达这件事说清楚', async ({ page, request }) => {
    await request.get(`${mockControl}?mode=provider-down&provider=openai`);
    try {
      await seedSession(page);
      await page.goto('/governance/providers');
      await page.getByRole('button', { name: '测试连接' }).click();
      await expect(page.getByRole('status')).toContainText('ConnectError', { timeout: 15_000 });
    } finally {
      await request.get(`${mockControl}?mode=provider-up`);
    }
  });
});
