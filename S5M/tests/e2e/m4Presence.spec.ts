import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// UM-AC-21, inverted: the three M4 pages are rendered, at both viewports, from a deep link — not
// answered by the catch-all 404. This file used to assert the opposite (the MVP carve-out,
// docs/adr/004-m4-carve-out.md); S1 CP-UI-023 delivered the milestone, so the coverage now checks that
// the pages are there and that they show what S1 actually returned.
//
// The static half (route table, allowed-actions gating, idempotency intents, both locales) lives in
// src/test/m4Presence.test.ts. The UM-AC-20 clause below — no control on the API review surface may
// reach a target instance — is unchanged and still belongs with this pair of screens.

const PAGES = [
  {
    path: '/projects/proj-1/members',
    title: '项目成员',
    heading: '成员',
    // Rows the harness serves: the list must show the server's rows, not an empty shell.
    fact: 'ada@example.org',
  },
  {
    path: '/projects/proj-1/grants',
    title: '受限读取授权',
    heading: '授权记录',
    // the word the phone shows for PHI_PII (`inputs.classPhi`), not the wire token
    fact: '个人健康/身份信息（PHI/PII）',
  },
  {
    path: '/projects/proj-1/exemptions',
    title: '生产豁免',
    heading: '豁免记录',
    fact: '只在非真实环境核验过',
  },
];

const VIEWPORTS: Record<string, { width: number; height: number }> = {
  '393px 移动视口': { width: 393, height: 760 },
  '1280px 桌面视口': { width: 1280, height: 900 },
};

for (const [name, viewport] of Object.entries(VIEWPORTS)) {
  test.describe(name, () => {
    test.use({ viewport });

    test('深链渲染出页面自己的标题，而不是 404', async ({ page }) => {
      await seedSession(page);
      for (const target of PAGES) {
        await page.goto(target.path);
        await expect(page.locator('.shell__topbar')).toContainText(target.title, { timeout: 15_000 });
        // The page's own heading, i.e. it mounted rather than falling through to the catch-all route.
        await expect(page.getByText(target.title, { exact: true }).first()).toBeVisible();
        await expect(page.getByText(target.heading, { exact: true }).first()).toBeVisible();
        await expect(page.getByText('404')).toHaveCount(0);
        await expect(page.getByText(target.fact, { exact: true }).first()).toBeVisible();
      }
    });

    test('项目详情把三页都挂在导航里', async ({ page }) => {
      await seedSession(page);
      await page.goto('/projects/proj-1');
      await expect(page.getByRole('heading', { name: '阶段进度' })).toBeVisible({ timeout: 15_000 });
      for (const target of PAGES) {
        await expect(page.getByRole('button', { name: target.title })).toBeVisible();
      }
    });

    test('豁免页写出到服务端，再从服务端读回来', async ({ page, request }) => {
      // The write path of the three, end to end: an exemption is the one whose form needs no account
      // list, so it can be filled from the harness alone. The row that appears afterwards came from
      // the mock's mutated table — a stub that only echoed the body would pass this and still be a
      // page that never shows what the server recorded.
      await seedSession(page);
      await request.get('http://127.0.0.1:8787/api/v1/_control?mode=m4-reset');
      await page.goto('/projects/proj-1/exemptions');
      await expect(page.getByText('豁免记录')).toBeVisible({ timeout: 15_000 });

      // A run-specific reason, and assertions scoped to the row that carries it: every project in a
      // run shares one harness, so a global standing-count comparison passed in one project and
      // failed in the next for reasons that had nothing to do with the page under test.
      const reason = `e2e 冒烟：非生产校验暂不可用 ${Date.now()}`;
      await page.getByRole('button', { name: '登记豁免' }).first().click();
      await page.locator('.fui-sheet').getByRole('button', { name: /DEV/ }).click();
      await page.locator('.fui-sheet textarea').fill(reason);
      await page.locator('.fui-sheet__footer button').click();

      const ourRow = page.locator('.ae__row', { hasText: reason });
      await expect(ourRow).toBeVisible({ timeout: 15_000 });
      await expect(ourRow.getByRole('button', { name: '撤销' })).toBeVisible();

      await ourRow.getByRole('button', { name: '撤销' }).click();
      // The harness keeps the row and stamps it revoked, which is what S1 does for the audit trail:
      // the row stays listed, its revocation control goes away and the tag says 已撤销.
      await expect(ourRow.getByRole('button', { name: '撤销' })).toHaveCount(0, { timeout: 15_000 });
      await expect(ourRow.getByText('已撤销')).toBeVisible();
    });

    test('API 审核页没有任何可发起请求的控件', async ({ page }) => {
      await seedSession(page);
      await page.goto('/projects/proj-1/api');
      await page.waitForTimeout(800);
      const controls = await page.locator('button, [role="button"], input, select').allInnerTexts();
      const offenders = controls.map((t) => t.trim()).filter((t) => /试运行|试一试|试一下|发送|send|try.?it|execute|运行请求/i.test(t));
      expect(offenders, `API review surface exposes: ${offenders.join(' | ')}`).toEqual([]);
    });
  });
}