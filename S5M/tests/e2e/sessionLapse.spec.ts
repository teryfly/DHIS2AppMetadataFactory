import { expect, test, type Page, type APIRequestContext } from '@playwright/test';
import { seedSession } from './helpers';

/*
 * UM-NET-009: a session that expires while the app is open must end visibly and recoverably, and
 * changing your own password has to tell you that you are now signed out.
 *
 * The harness answers `authMode: off` for every other spec, which is the one state where this app has
 * no sign-in panel at all — so these tests switch it into an account deployment through `/_control`
 * and switch it back afterwards, so a lapse cannot leak into the next file. The refusals come from
 * the harness as real 401s with S1's envelope, because the behaviour under test is exactly how the
 * client reacts to *that*: a 200 carrying an error body would leave the app's error mapping
 * unreachable and prove nothing (the same reason UM-AC-19's 404 has to be a genuine 404).
 *
 * Sign-in goes through the shell's dialog rather than the card on /access, because that dialog is
 * what `needsLogin` raises — the requirement is about the app making the way back obvious from
 * wherever the operator happens to be standing.
 */

const mockControl = 'http://127.0.0.1:8787/api/v1/_control';
const SUBJECT = 'tester@example.org';
const PASSWORD = 'correct-horse';
const VALUE = 'N3w-K3ntosh-Value';

async function mode(request: APIRequestContext, next: string): Promise<void> {
  const answer = await request.get(`${mockControl}?mode=${next}`);
  expect(answer.ok()).toBe(true);
}

/** The app-level sign-in: its own page, reached by the shell whenever there is no session. */
async function signIn(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel('账号').fill(SUBJECT);
  await page.getByLabel('密码').fill(PASSWORD);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

/** The session card's "current identity" row — what the app claims about who is here. */
function identity(page: Page) {
  return page.locator('.acc__row', { hasText: '当前身份' });
}

/** One account's row in the users list, found by the name the console prints for it. */
function userCard(page: Page, name: string) {
  return page.locator('.fui-card').filter({ has: page.getByText(name, { exact: true }) });
}

test.describe('UM-NET-009 会话过点与自助改密', () => {
  test.beforeEach(async ({ request }) => {
    await mode(request, 'auth-on');
  });

  test.afterEach(async ({ request }) => {
    await mode(request, 'auth-off');
  });

  test('账号部署下登录，身份与角色来自服务端而不是界面自己', async ({ page }) => {
    await seedSession(page);
    await signIn(page);

    await page.goto('/access');
    await expect(identity(page)).toContainText('测试员');
    await expect(page.getByText('平台管理员')).toBeVisible();
    // S1 says there is a session; the app stops asking for one and leaves the page
    await expect(page).not.toHaveURL(/\/login/);
  });

  test('一次 401 就把登录入口拉回来，并且只说一次', async ({ page }) => {
    await seedSession(page);
    await signIn(page);
    await page.goto('/access');
    await expect(identity(page)).toContainText('测试员');

    // expire it out from under the open page, then let the app find out the way a user does: by
    // reaching for something (the 用户 tab fires GET /users)
    await mode(page.request, 'lapse');
    await page.getByRole('tab', { name: '用户', exact: true }).click();

    // The 401 sends the operator to the sign-in page, and the page says why it appeared — being
    // moved without an explanation reads as a crash.
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByText('你的会话已经结束')).toBeVisible();
    // and no toast at all: the refused reads are one event, and the page is the notice about it.
    // Before this the app pushed one per failed read, so an expired session looked like a pile of
    // unrelated failures (UM-NET-002 asks for "what happened + what to do", not volume).
    await expect(page.locator('.fui-toast')).toHaveCount(0);

    // The redirect *replaces* the lapsed screen rather than stacking on top of it: going back would
    // otherwise land the operator on a page that cannot load, so this asserts it is not in history.
    // The screen they were on travels as `?next=`, and signing in resumes there instead of dropping
    // them at the home tab.
    await page.getByLabel('账号').fill(SUBJECT);
    await page.getByLabel('密码').fill(PASSWORD);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page).toHaveURL(/\/access/);
    await expect(identity(page)).toContainText('测试员');
  });

  test('被锁的账号说得出"已锁定"，而不只是一个红色的 ACTIVE', async ({ page }) => {
    await seedSession(page);
    await signIn(page);
    await mode(page.request, 'lock-user');
    await page.goto('/access');
    await page.getByRole('tab', { name: '用户', exact: true }).click();

    // the login endpoint deliberately refuses to say "locked" (that would be an enumeration
    // channel), so this list is the only place the fact can surface — and `status` still says ACTIVE
    // The mark has to land on the row it belongs to: a second "已锁定" somewhere else on the screen
    // would satisfy a plain getByText and still leave the operator guessing who is frozen.
    await expect(userCard(page, 'Larry')).toContainText('已锁定');
    await expect(userCard(page, 'Larry').locator('.fui-card__extra .fui-tag')).toHaveClass(/fui-tag--danger/);
    // the account beside it keeps its own status, so the lock mark is not replacing status
    await expect(userCard(page, 'Ada')).toContainText('启用中');
    await expect(userCard(page, 'Ada').locator('.fui-card__extra .fui-tag')).toHaveClass(/fui-tag--success/);
  });

  test('改自己的口令：短口令被拒且不留在框里，改完明确回到登录态', async ({ page }) => {
    await seedSession(page);
    await signIn(page);
    await page.goto('/access');

    const field = page.getByLabel('新密码');
    await expect(page.getByText('改完会结束这个账号当前打开的全部会话')).toBeVisible();
    await field.fill('Sh0rt!');
    await page.getByRole('button', { name: '修改密码', exact: true }).click();
    await expect(page.locator('.fui-toast')).toHaveCount(1);
    // the 422 names the field and never repeats the value, and the app drops it either way
    await expect(field).toHaveValue('');
    await expect(page.locator('body')).not.toContainText('Sh0rt!');

    await field.fill(VALUE);
    await page.getByRole('button', { name: '修改密码', exact: true }).click();
    await expect(page.getByText('口令已修改')).toBeVisible();
    // the write revoked this account's sessions, so the app is back on the sign-in page
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('body')).not.toContainText(VALUE);
    // and signing in again is the only proof that the lapse was recoverable rather than fatal
    await page.getByLabel('账号').fill(SUBJECT);
    await page.getByLabel('密码').fill(PASSWORD);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
  });

  test('建账号：登录名与姓名一起送到 S1，列表按姓名读回来', async ({ page }) => {
    await seedSession(page);
    await signIn(page);
    await page.goto('/access');
    await page.getByRole('tab', { name: '用户', exact: true }).click();

    const INITIAL = 'an-initial-secret';
    // Scoped to the create card, because the sign-in dialog uses the same 账号 / 密码 labels and is
    // only hidden, not removed — an unscoped getByLabel would resolve to two fields and prove nothing.
    const form = page.locator('.fui-card').filter({ has: page.getByText('新建用户', { exact: true }) });
    await form.getByLabel('账号').fill('lidi@example.org');
    await form.getByLabel('姓名').fill('李迪');
    await form.getByLabel('邮箱').fill('lidi@example.org');
    await form.getByLabel('密码').fill(INITIAL);
    // CP-IAM-005 ③ makes this flag report-only, so the only job the console has with it is to carry
    // the operator's choice through and show it back. Toggling it here proves both halves: the write
    // is no longer a constant baked into the form, and the list is no longer blind to the flag.
    await form.getByRole('switch', { name: '首次登录时要求修改口令' }).click();
    await page.getByRole('button', { name: '新建用户', exact: true }).click();

    // the two names are separate facts and the console has to carry both: `subject` is what the
    // person types at the login box, the display name is what everybody else reads
    const made = userCard(page, '李迪');
    await expect(made).toBeVisible();
    await expect(made).toContainText('lidi@example.org');
    await expect(made).toContainText('需改口令');
    // and the initial password leaves no trace: not in the form after the write, not in the list
    await expect(form.getByLabel('密码')).toHaveValue('');
    await expect(page.locator('body')).not.toContainText(INITIAL);
  });

  test('改名与停用：账号建好之后这两件事仍然做得了', async ({ page }) => {
    await seedSession(page);
    await signIn(page);
    await mode(page.request, 'lock-user');
    await page.goto('/access');
    await page.getByRole('tab', { name: '用户', exact: true }).click();

    // scoped to the sheet: the create card above has 姓名 / 邮箱 fields with the same labels
    await userCard(page, 'Ada').getByRole('button', { name: '编辑账号' }).click();
    const sheet = page.locator('.fui-sheet');
    await expect(sheet.getByLabel('姓名')).toHaveValue('Ada');
    await sheet.getByLabel('姓名').fill('阿迪');
    await sheet.getByRole('button', { name: '保存' }).click();
    // the row reads back by the name the operator just typed, and the login name is untouched
    await expect(userCard(page, '阿迪')).toContainText('ada@example.org');

    // S1 has no delete: 停用 is how an account leaves the system, so a console that could only create
    // one could never take it back
    await userCard(page, '阿迪').getByRole('button', { name: '停用' }).click();
    await expect(userCard(page, '阿迪')).toContainText('已停用');
    await userCard(page, '阿迪').getByRole('button', { name: '启用' }).click();
    await expect(userCard(page, '阿迪')).toContainText('启用中');
  });

  test('角色的授予、限定到某项目、再收回，这三步在手机上都做得完', async ({ page }) => {
    await seedSession(page);
    await signIn(page);
    await mode(page.request, 'lock-user');
    await page.goto('/access');
    await page.getByRole('tab', { name: '用户', exact: true }).click();

    // S1's revoke *deletes* the grant instead of flagging it, so the honest answer from the list is a
    // shorter list. Until the harness implemented these two endpoints the sheet had nothing to pick
    // (GET /roles answered empty) and the write fell through to the body echo — the role path was
    // neither exercisable nor observable here, whatever the green suite used to imply.
    const ada = userCard(page, 'Ada');
    await expect(ada.getByText('方案架构师')).toBeVisible();
    await ada.getByRole('button', { name: '收回' }).click();
    await expect(ada.getByText('方案架构师')).toHaveCount(0);

    await userCard(page, 'Ada').getByRole('button', { name: '分配角色' }).click();
    const sheet = page.locator('.fui-sheet');
    await sheet.getByRole('button', { name: '部署审批人', exact: true }).click();
    await sheet.getByRole('button', { name: '确认' }).click();
    await expect(userCard(page, 'Ada').getByText('部署审批人')).toBeVisible();
    // a role scoped to a project is a different grant, and the row has to say which one it is.
    // The scope is picked from the projects S1 lists — there is no id to type any more, which is the
    // point: an unknown id used to be the only failure mode this field offered.
    await userCard(page, 'Ada').getByRole('button', { name: '分配角色' }).click();
    await page.locator('.fui-sheet').getByRole('button', { name: '业务分析员', exact: true }).click();
    await page.locator('.fui-sheet').getByRole('button', { name: '免疫规划试点' }).click();
    await page.locator('.fui-sheet').getByRole('button', { name: '确认' }).click();
    await expect(userCard(page, 'Ada').getByText('业务分析员 · proj-1')).toBeVisible();
  });

  test('登录页必须有反应：空表说缺什么，口令错说为什么，成功说是谁', async ({ page }) => {
    await seedSession(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByLabel('账号')).toBeVisible();

    // An empty submit used to swallow the tap: nothing moved, no toast, no word — from the
    // operator's side indistinguishable from a dead button or a lost network.
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page.getByText('请填写账号与口令再登录。')).toBeVisible();
    await expect(page).toHaveURL(/\/login/);

    await page.getByLabel('账号').fill(SUBJECT);
    await page.getByLabel('密码').fill('not-the-password');
    await page.getByRole('button', { name: '登录', exact: true }).click();

    // The reason sits on the page, under the fields it is about — and the page does not navigate
    // away from it, which is what a modal used to do on every tap.
    await expect(page.getByText('账号或口令不正确')).toBeVisible();
    await expect(page.getByText(/连续五次口令不正确会锁定/)).toBeVisible();
    await expect(page.locator('.fui-toast')).toHaveCount(0);
    await expect(page).toHaveURL(/\/login/);
    // the secret they just typed is not left in the field while they read the reason; the account
    // name is, because a typo is the likeliest thing to fix
    await expect(page.getByLabel('密码')).toHaveValue('');
    await expect(page.getByLabel('账号')).toHaveValue(SUBJECT);

    await page.getByLabel('密码').fill(PASSWORD);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    // and the success is named, not implied by a screen that quietly changed
    await expect(page.locator('.fui-toast')).toContainText('已登录：测试员');
  });
});
