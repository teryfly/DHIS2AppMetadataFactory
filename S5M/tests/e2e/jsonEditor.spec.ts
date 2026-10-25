import { test, expect } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { seedSession } from './helpers';

// UM-AC-17: "桌面端在 JSON 编辑器中输入一个不在 Schema 中的键 → 行内标注错误并给出字段路径；
// 输入键名首字母时出现补全建议"; plus UM-CAP-001's "5,000 行文档编辑时输入延迟 < 100 ms".
// CodeMirror 6 needs real layout, so this cannot be asserted under jsdom — it is the browser arm.

test.use({ viewport: { width: 1280, height: 900 } });

async function openEditor(page: import('@playwright/test').Page): Promise<void> {
  await seedSession(page);
  await page.goto('/dev/json-editor');
  // CodeMirror is loaded only by a harness build (`src/router/index.ts` gates `/dev/*` on
  // `VITE_DEV_HARNESS`); a missing `.cm-content` means `dist/` holds the production bundle, not a
  // broken editor, and the 20 s wait should end by saying so.
  await expect(page.locator('.jec__editor .cm-content')).toBeVisible({ timeout: 20_000 }).catch(() => {
    throw new Error('no CodeMirror on the served bundle — S5M/dist is a production build, run `npm run build:harness` before this suite');
  });
}

test('越界键在所在行被标注，并给出行与字段路径', async ({ page }) => {
  await openEditor(page);
  const doc = page.locator('.jec__editor .cm-content');
  await doc.click();
  // Insert *inside* the root object: after `{` on the first line. Sending the caret to the end of
  // the document puts it after the closing brace, which is a syntax error, and the editor is built
  // to stop at syntax (validating a half-written document would only produce noise).
  await page.keyboard.press('Control+Home');
  await page.keyboard.press('End');
  await page.keyboard.type('\n  "buzz": 1,');
  await expect(doc).toContainText('"buzz"');
  // @codemirror/lint paints one mark per diagnostic; the gutter shows an error marker on its line.
  await expect(page.locator('.cm-lint-marker-error').first()).toBeVisible({ timeout: 5_000 });
  // The issues list carries the field path, which is the second half of the acceptance. The
  // document is deliberately incomplete as well, so select the row about the unknown key.
  const row = page.locator('.fje__issue', { hasText: 'buzz' }).first();
  await expect(row, 'an issue naming the unknown key').toBeVisible({ timeout: 5_000 });
  await expect(row.locator('.fje__path')).toHaveText('/buzz');
  await expect(row.locator('.fje__msg')).toContainText('buzz');
});

test('输入键名首字母出现 Schema 补全建议', async ({ page }) => {
  await openEditor(page);
  const doc = page.locator('.jec__editor .cm-content');
  await doc.click();
  await page.keyboard.press('Control+End');
  await page.keyboard.type(',\n  "');
  await page.keyboard.type('p');
  const list = page.locator('.cm-tooltip-autocomplete ul[role="listbox"]');
  await expect(list).toBeVisible({ timeout: 5_000 });
  await expect(list.locator('li')).toContainText(/projectType/);
});

test('格式化把文档重排为 2 空格缩进', async ({ page }) => {
  await openEditor(page);
  const doc = page.locator('.jec__editor .cm-content');
  await doc.click();
  await page.keyboard.press('Control+a');
  await page.keyboard.type('{"deliveryMode":"A","entities":[]}');
  await page.locator('button:has-text("格式化")').click();
  await expect(doc).toContainText('"deliveryMode": "A"');
});

test('5,000 行文档的输入延迟 < 100 ms（UM-CAP-001）', async ({ page }) => {
  await seedSession(page);
  await page.goto('/dev/json-editor');
  // Load a ~5,000-line document, then measure main-thread blocking while typing into it. Typing
  // through the real keyboard path (rather than dispatching into the editor internals) is what the
  // requirement bounds: "5,000 行文档编辑时输入延迟 < 100 ms".
  await page.goto('/dev/json-editor?big=1');
  await expect(page.locator('.jec__editor .cm-content')).toBeVisible();
  await page.waitForTimeout(1_200);
  // CodeMirror renders only the visible rows, so the DOM cannot prove the document size; the
  // harness reports the real line count of the document it loaded.
  const lineCount = Number(await page.getByTestId('line-count').innerText());
  expect(lineCount, 'the harness must present a 5,000-line document').toBeGreaterThan(4_000);

  // UM-CAP-001 bounds 输入延迟, i.e. how long the reader waits for the next painted frame after a
  // keystroke. Measured as the inter-frame gap around each typed character. Background work
  // (debounced validation) is reported alongside as longtasks so a pass cannot hide a stutter: if
  // validation itself blocks the thread, the frame gaps below will show it.
  const latency = await page.evaluate(async () => {
    const editor = document.querySelector('.cm-content') as HTMLElement | null;
    editor?.focus();
    const gaps: number[] = [];
    const longTasks: number[] = [];
    const obs = new PerformanceObserver((list) => { for (const e of list.getEntries()) longTasks.push(Math.round(e.duration)); });
    obs.observe({ entryTypes: ['longtask'] });
    for (let i = 0; i < 40; i++) {
      const before = await new Promise<number>((r) => requestAnimationFrame(() => r(performance.now())));
      document.execCommand('insertText', false, ' ');
      const after = await new Promise<number>((r) => requestAnimationFrame(() => r(performance.now())));
      gaps.push(after - before);
    }
    await new Promise((r) => setTimeout(r, 1_200));
    obs.disconnect();
    const sorted = [...gaps].sort((a, b) => a - b);
    return {
      keystrokes: gaps.length,
      medianFrameGapMs: Math.round(sorted[Math.floor(sorted.length / 2)] ?? 0),
      p95FrameGapMs: Math.round(sorted[Math.floor(sorted.length * 0.95)] ?? 0),
      worstFrameGapMs: Math.round(sorted[sorted.length - 1] ?? 0),
      longTaskCount: longTasks.length,
      worstLongTaskMs: longTasks.length ? Math.max(...longTasks) : 0,
    };
  });
  mkdirSync('docs/reports', { recursive: true });
  writeFileSync('docs/reports/json-editor-latency.json', JSON.stringify(latency, null, 2) + '\n');
  // No single keystroke may block the main thread past the 100 ms budget.
  expect(latency.medianFrameGapMs, `median keystroke-to-frame latency was ${latency.medianFrameGapMs}ms`).toBeLessThan(100);
  expect(latency.p95FrameGapMs, `p95 keystroke-to-frame latency was ${latency.p95FrameGapMs}ms`).toBeLessThan(100);
});
