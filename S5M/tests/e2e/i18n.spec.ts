import { expect, test } from '@playwright/test';
import { seedSession } from './helpers';

// UM-AC-16: "切换语言不需要刷新页面" and no interface string may be hard-coded. The static half of
// the second clause lives in src/test/i18nLiterals.test.ts (production sources contain no CJK
// literal at all); this file proves the runtime half — a single click re-renders every label, and
// the document keeps living (the flag set before the click is still there after it).

test.describe('UM-AC-16 语言切换', () => {
  test('一次点击改掉全部界面文案，页面不重载', async ({ page }) => {
    await seedSession(page);
    await page.goto('/me');
    await expect(page.getByText('语言')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.shell__topbar')).toContainText('我的');

    await page.evaluate(() => { (window as unknown as { __s5mAlive: number }).__s5mAlive = 1; });
    await page.getByText('English', { exact: true }).click();

    await expect(page.getByText('Language')).toBeVisible();
    await expect(page.locator('.shell__topbar')).toContainText('Me');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');

    // Same window object ⇒ the transition was Vue re-rendering, not a navigation.
    expect(await page.evaluate(() => (window as unknown as { __s5mAlive?: number }).__s5mAlive)).toBe(1);
    expect(await page.evaluate(() => localStorage.getItem('s5m:language'))).toBe(JSON.stringify('en'));
  });

  test('英文界面可切回中文，同样不需要刷新', async ({ page }) => {
    // The entry language is pinned through seedSession rather than switched and then navigated
    // away from: addInitScript re-runs on every document load, so a switch would be undone.
    await seedSession(page, 'en');
    await page.goto('/projects');
    await expect(page.getByText('New project', { exact: true })).toBeVisible({ timeout: 15_000 });

    await page.goto('/me');
    await page.getByText('简体中文', { exact: true }).click();
    await expect(page.locator('.shell__topbar')).toContainText('我的');
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
    await expect(page.getByText('Language')).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem('s5m:language'))).toBe(JSON.stringify('zh-CN'));
  });
});
