import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { seedSession } from './helpers';

// UM-NAV-004 in the browser: the top search reaches four kinds of thing and a hit has to be a jump,
// and the "recently visited" half of the panel has to show what the operator actually opened.
//
// The routing table itself is pinned in src/lib/search.test.ts; what only a real run can show is
// that the panel reads the server's rows, that the group titles appear in the chosen language, and
// that the object hit lands on the graph page with the node selected rather than on a blank canvas.
// The stage and object groups have no cross-project endpoint on S1, so they search the project this
// operator last opened (see nav.searchScopeHint). A test that wants them has to open one first,
// exactly as a person would; going straight to the panel from a cold load only ever offers projects
// and environments, which is the designed answer rather than a missing result.
async function openSearch(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: '搜索' }).click();
  return page.locator('.fui-overlay-panel');
}

test.describe('顶部搜索与最近访问（UM-NAV-004）', () => {
  test.beforeEach(async ({ page }) => {
    await seedSession(page);
  });

  test('按项目名搜到并跳过去', async ({ page }) => {
    await page.goto('/');
    const panel = await openSearch(page);
    await panel.getByPlaceholder('搜索项目 / 阶段 / 环境 / businessKey').fill('免疫');
    await expect(panel.getByText('项目', { exact: true })).toBeVisible();
    await panel.getByText('免疫规划试点').click();
    await expect(page).toHaveURL(/\/projects\/proj-1$/, { timeout: 10_000 });
  });

  test('businessKey 命中后落到图上并选中那个对象', async ({ page }) => {
    await page.goto('/projects/proj-1');
    const panel = await openSearch(page);
    await panel.getByPlaceholder('搜索项目 / 阶段 / 环境 / businessKey').fill('dataelement.a');
    await expect(panel.getByText('对象', { exact: true })).toBeVisible();
    await panel.getByText('dataelement.a').first().click();
    await expect(page).toHaveURL(/graph\?businessKey=dataelement\.a/, { timeout: 10_000 });
    // The page had to consume the query; a graph that ignores it looks identical until you look for
    // the selected node's own panel. Scoped to that panel on purpose: the key is also a row in the
    // node list, and "somewhere on the screen" would pass even if nothing had been selected.
    const detail = page.locator('.gph__detail');
    await expect(detail).toContainText('dataelement.a');
    await expect(detail).toContainText('UID');
  });

  test('没输入时给的是最近访问，而不是一个空列表', async ({ page }) => {
    await page.goto('/');
    const panel = await openSearch(page);
    await expect(panel.getByText('还没有访问记录')).toBeVisible();
    await page.goto('/projects/proj-1');
    const again = await openSearch(page);
    await expect(again.getByText('最近访问')).toBeVisible();
    // The stored row is a project id; the panel resolves it to the name the list answers with.
    await expect(again.getByText('免疫规划试点').first()).toBeVisible();
  });
});
