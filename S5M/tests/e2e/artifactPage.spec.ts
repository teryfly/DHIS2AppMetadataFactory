import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// UM-REV-001's two artifact controls, on a real page against the harness S1: 下载 and 复制链接.
// They used to be one button labelled "下载 / 复制链接" that only copied — the operator pressing it
// for the bytes got a clipboard and nothing else. The pair is now separate, the download is a real
// address S1 names (CP-UI-016), and 清除内容 (FR-DATA-008) is confirmed by typing, per UM-P-04.
const CONTROL = 'http://127.0.0.1:8787/api/v1/_control';
const ARTIFACT = '/artifacts/art-openapi';

test.describe('产物详情：下载、复制链接与清除', () => {
  test.beforeEach(async ({ request }) => {
    await request.get(`${CONTROL}?mode=artifact-intact`);
  });
  test.afterEach(async ({ request }) => {
    // Purging is a one-way fact about the harness's row; leaving it set would make the next file's
    // "the download link is there" assertion fail for something it never did.
    await request.get(`${CONTROL}?mode=artifact-intact`);
  });

  test('字节有自己的下载地址，链接是页面地址', async ({ page }) => {
    await seedSession(page);
    await page.goto(ARTIFACT);
    const dl = page.getByRole('link', { name: '下载' });
    await expect(dl).toHaveAttribute('href', /\/artifacts\/art-openapi\/content\?download=true$/);
    await expect(page.getByRole('button', { name: '复制链接' })).toBeVisible();
    // The card reads the server's row, not an empty envelope: this is the fixture the page had never
    // been shown before (the harness used to answer the detail read with a bare `{items:[]}`). Exact,
    // because `getByText` is a case-insensitive substring match and the openapi body this artifact
    // carries mentions the same word.
    await expect(page.getByText('接口定义', { exact: true })).toBeVisible();
    await expect(page.getByText(/^sha256:art1/).first()).toBeVisible();
  });

  test('清除内容要先输入产物 ID 才按得下去，清完只剩记录', async ({ page }) => {
    await seedSession(page);
    await page.goto(ARTIFACT);
    await page.getByRole('button', { name: '清除内容' }).click();
    const sheet = page.locator('.fui-sheet');
    await expect(sheet.getByText('这一步不可逆')).toBeVisible();

    const confirm = sheet.getByRole('button', { name: '确认' });
    await sheet.getByPlaceholder('art-openapi').fill('art-opena');
    await expect(confirm).toBeDisabled();

    await sheet.getByPlaceholder('art-openapi').fill('art-openapi');
    await expect(confirm).toBeEnabled();
    await confirm.click();

    // The banner is the fact; the success toast carries the same words for 2.6 s, so a bare text
    // match is a coin flip on that overlap. Ask for the thing that stays on screen.
    const purgedBanner = page.locator('.fui-notice');
    await expect(purgedBanner).toContainText('内容已清除');
    // After the purge there is nothing to download: the control goes away rather than starting a
    // request that S1 would answer with 410.
    await expect(page.getByRole('link', { name: '下载' })).toHaveCount(0);

    await page.reload();
    await expect(page.locator('.fui-notice')).toContainText('内容已清除');
  });
});
