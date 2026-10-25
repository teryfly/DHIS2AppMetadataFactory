import { expect, type Page } from '@playwright/test';

/**
 * AppShell opens the operator-name dialog on a cold profile (UM-AC-01). It is a modal Vant popup,
 * which locks `body` with `van-overflow-hidden` and swallows every pointer event — enough to make a
 * gesture benchmark silently measure nothing. Seed the identity instead of dismissing the dialog,
 * because that is also what a real returning user has.
 */
export async function seedSession(page: Page, language = 'zh-CN'): Promise<void> {
  // `addInitScript` stringifies the function and evaluates it in the page, so an outer variable
  // cannot be closed over — `language` has to arrive as the script's argument.
  //
  // Both values are stored as JSON by the client (`lib/storage.ts:writeJSON`, and the stores read
  // them back with `readJSON`), so a bare string here parses to nothing and the operator stays
  // empty — which silently disabled every gated control the harness was supposed to exercise.
  await page.addInitScript((lang) => {
    localStorage.setItem('s5m:operator', JSON.stringify('测试员'));
    sessionStorage.setItem('s5m:name-prompted', '1');
    localStorage.setItem('s5m:language', JSON.stringify(lang));
  }, language);
}

/** Fail fast if any modal is still covering the page. */
export async function assertNoOverlay(page: Page): Promise<void> {
  expect(await page.locator('body.van-overflow-hidden').count(), 'a Vant overlay is still open').toBe(0);
}
