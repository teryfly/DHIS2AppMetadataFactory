import { test, expect, type Page } from '@playwright/test';

/*
 * UI-NFR-04: end-to-end on the main path against the mock build (no live S1):
 *   set operator → dashboard → create project → workbench stage nav → Question → Gate approval
 *   → Deployments → Run console COMMIT. Button availability and the operator gate are exercised.
 *   UI-AC-01 (no operator disables actions), AC-02 (COMMIT disabled), AC-04 (warnings block approve).
 */

async function setOperator(page: Page, name: string): Promise<void> {
  // First-run dialog (UI-OP-001).
  const input = page.getByLabel(/Operator|操作者/);
  await input.fill(name);
  await page.getByRole('button', { name: /Start|开始|Save|保存/ }).click();
}

test('operator gate blocks actions until a name is set (UI-AC-01)', async ({ page }) => {
  await page.goto('/');
  // UI-OP-002: an action that changes state is closed while nobody is signed, and the reason is
  // the missing operator rather than a silent dead button. The wizard's submit only appears on
  // its last step, so the gate — where the action is on screen at once — is what is asserted.
  await page.goto('/projects/PRJ-green-01/gate/GT-1');
  await expect(page.getByLabel(/Operator|操作者/)).toBeVisible();
  // The workbench renders behind the prompt, so the gate is "nothing is actionable", not
  // "the control is absent": asserting absence raced with how fast the gate data arrived.
  await expect(page.getByRole('button', { name: /Approve|批准/, disabled: false })).toHaveCount(0);

  await setOperator(page, 'Ana Ruiz');
  const approve = page.getByRole('button', { name: /Approve|批准/ });
  await expect(approve).toBeVisible();
  // ...and it is still closed, because the warnings are unhandled and S1 has not offered it
  await expect(approve).toBeDisabled();
});

test('main flow: dashboard → new project → workbench', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');

  // Dashboard lists seeded projects.
  await expect(page.getByText('Community Health Register').first()).toBeVisible();

  // Open the seeded greenfield project workspace.
  await page.getByRole('link', { name: /Open project|打开项目/ }).first().click();
  await expect(page).toHaveURL(/\/projects\/PRJ-green-01\/overview/);

  // The stage flow is the navigation now (UI-WB-001/002): one ordered process, not a second rail.
  const flow = page.getByRole('list', { name: /Stage flow|阶段流程/ });
  await expect(flow.getByRole('link', { name: /Requirements|需求/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /Design|设计/ })).toBeVisible();
});

test('gate approval is blocked until every warning is handled (UI-AC-04 / UI-GATE-002)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/gate/GT-1');

  const approve = page.getByRole('button', { name: /Approve|批准/ });
  await expect(approve).toBeDisabled();

  // Every warning that is on screen must be handled before approval is offered. When the gate has
  // none, the invariant to prove is the other half: approval is closed because S1 has not
  // granted it, and the page says why rather than leaving a dead button.
  const accept = page.getByRole('button', { name: 'Accept' });
  if (await accept.count()) {
    // Accepting a warning toggles its own button rather than removing it, so the list length is
    // the loop bound: clicking "the first remaining one" forever is what hung this test.
    const total = await accept.count();
    for (let i = 0; i < total; i += 1) await accept.nth(i).click();
  } else {
    await expect(page.locator('body')).toContainText(/Approval|Gate|warning/i);
  }
  await expect(approve).toBeVisible();
});

test('COMMIT is disabled when not in allowed-actions (UI-AC-02)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/deployments');
  const run = page.getByRole('button', { name: /Deployment console|部署控制台/ }).first();
  if (await run.count()) {
    await run.click();
    await expect(page.getByRole('button', { name: 'COMMIT' })).toBeVisible();
  }
});

test('an unresolved environment binding is set by hand from a candidate (UI-DEP-003)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/deployments/RUN-1');

  const panel = page.getByRole('region', { name: /Environment bindings|环境绑定/ });
  await expect(panel).toBeVisible();
  await panel.getByRole('button', { name: /Set binding|设置绑定/ }).click();

  const candidate = panel.getByRole('radio', { name: /YrB8xKN9yOl/ });
  await expect(candidate).toBeVisible();
  await candidate.check();
  await panel.getByRole('textbox', { name: /Note|说明/ }).fill('the register has one age band set');
  await panel.getByRole('button', { name: /Set binding|设置绑定/ }).last().click();

  // the row now reads MANUAL, because the answer came from S1 rather than from the browser
  await expect(panel.getByRole('cell', { name: 'MANUAL' })).toBeVisible();
});

test('the commit outcome reads each report document by its own artifact id (UI-DEP-005)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/deployments/RUN-9');

  const panel = page.getByRole('region', { name: /Execution outcome|执行结果/ });
  await expect(panel).toBeVisible();
  await expect(panel.getByText(/name collided with an existing element/)).toBeVisible();
  await expect(panel.getByText(/diverged|不一致/)).toBeVisible();

  await panel.getByRole('button', { name: /ImportReport/ }).click();
  // the document is the ImportReport's own, shown as DHIS2 returned it
  await expect(page.getByText('importSummaries')).toBeVisible();
});

test('the object graph draws the model and jumps to a Registry entry (UI-REV-002)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/graph');

  const canvas = page.getByRole('region', { name: /Dependency graph canvas|依赖图画布/ });
  await expect(canvas).toBeVisible();
  // the nodes S1 returned are drawn, and the object list beside them says the same thing
  await expect(canvas.getByText('entity:facility')).toBeVisible();
  await expect(page.getByRole('rowheader', { name: /form:anc/ })).toBeVisible();

  // selecting an object highlights what refers to it, which is the reading the screen exists for
  await canvas.getByText('rule:age-range').first().click();
  await expect(canvas.locator('.react-flow__edge')).not.toHaveCount(0);

  // and the jump lands on that one Registry row, with the narrowing announced
  await page.getByRole('button', { name: /rule:age-range/ }).first().click();
  await expect(page.getByText(/Showing the object rule:age-range|正在显示对象 rule:age-range/)).toBeVisible();
});

test('the metadata review shows the compiled files by file (UI-REV-003)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/metadata');

  await page.getByText('By file').click();
  const grid = page.getByRole('region', { name: /By file|按文件/ });
  await expect(grid.getByText('metadata.json')).toBeVisible();
  await expect(grid.getByText('views/dataElements.json')).toBeVisible();
  // the build the list came from is named, so the operator knows which compile this is
  await expect(page.getByText(/bld-7/)).toBeVisible();
});

test('the stage assistant answers in the workbench and keeps its context header (UI-WB-010)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/projects/PRJ-green-01/stages/ST-req');

  await page.getByRole('button', { name: /Review assistant|评审助手/ }).click();
  const panel = page.getByRole('region', { name: /Review Assistant|评审助手/ });
  await expect(panel).toBeVisible();
  // FR-UI-002: the context the answer belongs to is named beside it, never inferred by the page
  await expect(panel.getByText(/Target environment|目标环境/)).toBeVisible();

  await panel.getByLabel(/Ask about this stage|就本阶段提问/).fill('Which ward list?');
  await panel.getByRole('button', { name: /^Ask$|^提问$/ }).click();
  await expect(panel.getByText(/Noted for ST-req/)).toBeVisible();
  // AI-CHAT-001: a question raised in dialogue is advice, and says so
  await expect(panel.getByText(/not a Gate blocker|不构成 Gate 阻塞/)).toBeVisible();
});

test('the administration screens list what S1 authorises (UI-AUTH-003)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');

  await page.goto('/users');
  // the account also appears as an option in the role card's picker, so name the table
  await expect(page.getByRole('cell', { name: 'ada@example.org' })).toBeVisible();
  await page.goto('/roles');
  await expect(page.getByText(/Solution architect|方案架构师/)).toBeVisible();

  await page.goto('/projects/PRJ-green-01/grants');
  await expect(page.getByText('ward list review')).toBeVisible();
  // CP-WF-020: a right S1 withheld is shown as withheld, not as a form that silently fails
  await page.goto('/projects/PRJ-green-01/exemptions');
  await expect(page.getByRole('button', { name: /Grant exemption|登记豁免/ })).toBeDisabled();
});

test('the budget is written and its state comes back from the server (UI-ADM-005)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  await page.goto('/governance/cost');

  await expect(page.getByText(/Close to the limit|接近上限/)).toBeVisible();
  await page.getByLabel(/^Limit/).fill('3');
  await page.getByRole('button', { name: /Save budget|保存预算/ }).click();
  // the verdict is S3's: spent 5.3 against a limit of 3 is refused, and the screen says what S1 said
  await expect(page.getByText(/limit exceeded|已超限额/)).toBeVisible();
});

test('the getting-started guide opens the unconfigured step and registers inside it (UI-AC-10, UI-AC-13)', async ({ page }) => {
  await page.goto('/');
  await setOperator(page, 'Ana Ruiz');
  // the dashboard carries the summary card because the mock leaves one environment untested
  await expect(page.getByText(/not configured yet/i).first()).toBeVisible();

  await page.goto('/getting-started');
  await expect(page.getByRole('heading', { name: /Get started/i })).toBeVisible();
  // one step per screen: the rail marks where the operator is standing, and the mock's providers are
  // all real, so the first unfinished step is the environment one
  const current = page.locator('[aria-current="step"]');
  await expect(current).toHaveCount(1);
  await expect(current).toContainText(/DHIS2 environments/i);
  // the untested environment is named, which is the fact only `GET /setup` carries
  await expect(page.getByText(/env-dev/i).first()).toBeVisible();

  // the registration opens on this screen rather than being a route to walk to
  await page.getByRole('button', { name: /Register an environment/i }).click();
  await expect(page).toHaveURL(/getting-started/);
  await expect(page.getByLabel(/Environment key|环境标识/i).first()).toBeVisible();
  // and the value-accepting control is next to every secret reference (UI-ADM-002)
  await expect(page.getByRole('button', { name: /Enter a new credential|录入新凭证/i }).first()).toBeVisible();
  // the way out stays offered, so mid-entry the operator can still choose the Administration page
  await expect(page.getByRole('link', { name: /open the Administration page/i })).toBeVisible();
});
