import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { seedSession } from './helpers';

/*
 * UM-AUTH-001: the organisation's single sign-on on a phone — the button, the trip out, and the
 * landing back.
 *
 * The desktop has had this since M1 and the mobile console had none of it: on an `authMode: both`
 * deployment a phone showed a password box and nothing else, so the account a deployment was built
 * around was unusable away from a desk. The requirement is the same one, so the screens are: the
 * entry point appears when S1 says the deployment has that door, the browser leaves for the
 * provider, and `/auth/callback` trades the returned code for a session.
 *
 * The provider is stood in for, deliberately: a real IdP is not in this test's reach, and the
 * behaviour under test is what *this* app does with the answer S1 gives it — the parameters it
 * builds, the state it echoes, the destination it honours afterwards. The exchange's own checks
 * (state against an HttpOnly cookie, a spent code) are S1's, and pytest covers them.
 */

const mockControl = 'http://127.0.0.1:8787/api/v1/_control';
const APP = 'http://127.0.0.1:4173';
const STATE = 'mock-state';
const CODE_BACK = `?code=mock-code&state=${STATE}`;

async function mode(request: APIRequestContext, next: string): Promise<void> {
  const answer = await request.get(`${mockControl}?mode=${next}`);
  expect(answer.ok()).toBe(true);
}

/**
 * Answer for the provider instead of refusing the navigation, and record what the app asked for.
 *
 * A fulfilled 302 is the obvious shape and was not used: `Location` would resolve against the
 * provider's origin (the mock, port 8787) and hand the browser to the stub server rather than back
 * into the app. A document that replaces its own location says the same thing without that ambiguity.
 */
async function standInForProvider(page: Page, backQuery: string): Promise<string[]> {
  const seen: string[] = [];
  await page.route('**/fake-idp/**', async (route) => {
    seen.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `<!doctype html><title>redirecting</title><script>location.replace(${JSON.stringify(`${APP}/auth/callback${backQuery}`)})</script>`,
    });
  });
  return seen;
}

test.describe('UM-AUTH-001 组织账号登录（S5-M）', () => {
  test.beforeEach(async ({ request }) => {
    await mode(request, 'oidc-on');
  });

  test.afterEach(async ({ request }) => {
    await mode(request, 'auth-off');
  });

  test('口令与组织登录并存时，两个入口都看得见；只有口令时不出现第二个', async ({ page }) => {
    await seedSession(page);
    await page.goto('/login');
    await expect(page.getByRole('button', { name: '使用组织账号登录' })).toBeVisible();
    await expect(page.getByLabel('账号')).toBeVisible();

    await mode(page.request, 'auth-on');
    await page.goto('/login');
    await expect(page.getByRole('button', { name: '使用组织账号登录' })).toHaveCount(0);
  });

  test('点一下就走：浏览器带着 S1 给的每个参数离开，回来时已经是登录态', async ({ page }) => {
    await seedSession(page);
    const seen = await standInForProvider(page, CODE_BACK);
    await page.goto('/login');
    await page.getByRole('button', { name: '使用组织账号登录' }).click();

    await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:4173\/$/);
    expect(seen).toHaveLength(1);
    const sent = new URL(seen[0] ?? '').searchParams;
    // every value comes from S1's answer — an invented one is a login the provider refuses, with an
    // error message that names neither the app nor the account
    expect(sent.get('client_id')).toBe('mock-client');
    expect(sent.get('redirect_uri')).toBe(`${APP}/auth/callback`);
    expect(sent.get('response_type')).toBe('code');
    expect(sent.get('scope')).toBe('openid profile email');
    expect(sent.get('state')).toBe(STATE);
    expect(sent.get('nonce')).toBe('mock-nonce');

    // signed in, with the same word the password form uses for it
    await expect(page.locator('.fui-toast')).toContainText('已登录：测试员');
  });

  test('被拦下来去做的事，登录完成后回到原地', async ({ page }) => {
    await seedSession(page);
    await standInForProvider(page, CODE_BACK);
    // the shell sends an unauthenticated operator to /login with the screen they wanted in `next`
    await page.goto('/access');
    await expect(page).toHaveURL(/\/login\?next=\/access/);
    await page.getByRole('button', { name: '使用组织账号登录' }).click();

    await expect(page).toHaveURL(/\/access/);
    await expect(page.locator('.acc__row', { hasText: '当前身份' })).toContainText('测试员');
  });

  test('state 不对就不算登录成功：说清楚，并留一条回去的路', async ({ page }) => {
    await seedSession(page);
    await standInForProvider(page, `?code=mock-code&state=not-the-state`);
    await page.goto('/login');
    await page.getByRole('button', { name: '使用组织账号登录' }).click();

    await expect(page).toHaveURL(/\/auth\/callback/);
    await expect(page.getByText('会话校验未通过')).toBeVisible();
    await expect(page.locator('.fui-toast')).toHaveCount(0);
    // the refusal is not a dead end
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
  });

  test('提供者在半路拒绝了，界面把它的意思原样说出来', async ({ page }) => {
    await seedSession(page);
    await standInForProvider(page, '?error=access_denied');
    await page.goto('/login');
    await page.getByRole('button', { name: '使用组织账号登录' }).click();

    await expect(page).toHaveURL(/\/auth\/callback/);
    await expect(page.getByText('登录被拒绝: access_denied')).toBeVisible();
    await expect(page).not.toHaveURL(/\/access|\/me/);
  });

  test('回调地址没有授权码时不空转，直接交回登录页', async ({ page }) => {
    await seedSession(page);
    await page.goto('/auth/callback');
    await expect(page.getByText('登录回调未携带授权码')).toBeVisible();
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
  });
});
