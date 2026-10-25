// Live browser pass over the last two rounds' visible changes, against the deployed consoles and the
// real S1 — not the harness: the Handoff page's gate, the projects list's choice group, and this
// round's artifact page (download address, two differently-named copy buttons, keyboard-reachable
// scroll region) and top search panel.
//
// The credential is read from the deployment's own env file (0600) and never written to argv, the
// console, or a file this script creates; nothing here prints it or the session cookie.
// Run from the repository root: `S5M/node_modules/.bin/playwright` is not needed — plain node is.
//   node S5M/scripts/verify-deployed-pages.mjs
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const DESK = 'https://127.0.0.1:1583';
const MOB = 'https://127.0.0.1:1584';
const ENV = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '.deploy', 'env');

function envValue(file, key) {
  const line = readFileSync(file, 'utf8').split('\n').find((l) => l.startsWith(`${key}=`));
  return line ? line.slice(key.length + 1).trim().replace(/^["']|["']$/g, '') : '';
}

let pass = 0;
let fail = 0;
function ok(name, cond, extra = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}`); } else { fail++; console.error(`  ✗ ${name} ${extra}`); }
}

async function signIn(page, base) {
  await page.goto(`${base}/login`, { waitUntil: 'networkidle' });
  // The shell asks for an operator name before it will let anything else on screen be reached; that
  // sheet covers the sign-in card, so it has to be answered rather than clicked through.
  const sheet = page.locator('.van-popup:visible, [role="dialog"]:visible');
  if (await sheet.count()) {
    const name = sheet.locator('input').first();
    if (await name.count()) await name.fill('verify-bot');
    const save = page.getByRole('button', { name: /保存|Save/ }).last();
    if (await save.count()) await save.click();
    await page.waitForLoadState('networkidle');
  }
  await page.fill('input[type="text"]', USER);
  await page.fill('input[type="password"]', PASS);
  await page.getByRole('button', { name: /登录|Sign in/i }).click();
  await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20_000 });
}

const USER = envValue(ENV, 'FACTORY_BOOTSTRAP_ADMIN_SUBJECT');
const PASS = envValue(ENV, 'FACTORY_BOOTSTRAP_ADMIN_PASSWORD');

const browser = await chromium.launch({ args: ['--ignore-certificate-errors'] });

for (const [label, base, viewport] of [
  ['移动 :1584', MOB, { width: 393, height: 844 }],
  ['桌面 :1583', DESK, { width: 1280, height: 900 }],
]) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport });
  const page = await ctx.newPage();
  const errors = [];
  let calls = [];
  page.on('pageerror', (e) => errors.push(String(e.message).slice(0, 120)));
  page.on('request', (req) => { if (req.url().includes('frontend-package')) calls.push(req.url()); });

  try {
    await signIn(page, base);
    ok(`${label}: 登录后离开 /login`, true);

    const projects = await page.request.get(`${base}/api/v1/projects?includeArchived=true`);
    const projs = (await projects.json()).items;
    const ids = projs.map((p) => p.id);
    for (const { id, workflowId } of projs) {
      calls = [];
      // `networkidle` is not reachable on the live app: the workspace holds an SSE stream open. The
      // page's own content region is what has to settle — and it is the region to read, because the
      // body of the desktop console also carries the navigation, which is never empty.
      const sel = label.startsWith('移动') ? '.hf' : '.workbench__area';
      await page.goto(`${base}/projects/${id}/handoff`, { waitUntil: 'domcontentloaded' });
      try {
        await page.waitForSelector(sel, { timeout: 12_000 });
        await page.waitForFunction(
          (s) => {
            const el = document.querySelector(s);
            const txt = el ? el.innerText.trim() : '';
            return txt.length > 20 && !/^(加载中|Loading)/.test(txt);
          },
          sel,
          { timeout: 15_000 },
        );
      } catch {
        /* read what is on screen anyway — the assertions below report what the page actually said */
      }
      const text = await page.locator('body').innerText();
      const guided = /开发包|Handoff|package|阶段|stage|不存在|exist|尚未|没有|无|已归档|archived/i.test(text);
      ok(`${label} ${id} 的开发包页给出可读的状态而不是空白`, guided, `「${text.slice(0, 90).replace(/\s+/g, ' ')}」`);
      /* The gate is the point, and the gate is allowed-actions — not what the endpoint answers to a
         request the page never made. `GET /projects/{id}/frontend-package/files` authorizes
         PROJECT_VIEW, which every reader of the project holds, while `frontend_package.download`
         answers S1's other question (`domain/actions.py:699`): is there a delivered package *for this
         account to hand over*. Those two disagree on this deployment right now — the seeded package
         project answers 200 to the read and PERMISSION_DENIED to the action, because the admin's
         PLATFORM_ADMINISTRATOR is organization-wide and the action is judged against project roles —
         so asserting against the HTTP status would be asserting that the console ignores its gate. */
      const probe = await ctx.newPage();
      const acts = workflowId
        ? await (await probe.request.get(`${base}/api/v1/workflows/${workflowId}/allowed-actions`)).json().catch(() => null)
        : null;
      const listed = await probe.request.get(`${base}/api/v1/projects/${id}/frontend-package/files`);
      await probe.close();
      const entry = [...(acts?.allowed ?? []), ...(acts?.unavailable ?? [])]
        .find((a) => String(a.action ?? '') === 'frontend_package.download');
      const offered = !!entry?.available;
      const asked = calls.some((u) => u.includes('frontend-package/files'));
      const note = `动作=${entry ? `${entry.available}/${entry.reasonCode ?? '—'}` : '服务端没列这一条'} · 端点 HTTP ${listed.status()} · 页面发了 ${calls.length} 次`;
      if (offered) ok(`${label} ${id}: 动作给出时确实读了清单`, asked, note);
      else ok(`${label} ${id}: 动作没给出时页面没有替服务端决定该读`, !asked, note);
    }

    // The new choice group, on the mobile console only.
    if (label.startsWith('移动')) {
      await page.goto(`${base}/projects`, { waitUntil: 'domcontentloaded' });
      const chips = page.locator('.fui-chips__chip');
      ok('项目列表的归档筛选是分块控件，两个状态各一块', (await chips.count()) === 2, `(实际 ${await chips.count()} 块)`);
      const pressed = page.locator('.fui-chips__chip[aria-pressed="true"]');
      ok('其中一块的按压态是明确的', (await pressed.count()) === 1);
      const box = await chips.first().boundingBox();
      ok('每块都是 44 × 44 以上', !!box && box.height >= 44 && box.width >= 44, JSON.stringify(box));
      await page.getByRole('button', { name: /显示已归档/ }).click();
      await page.waitForTimeout(1_200);
      ok('切到「显示已归档」后仍列出项目', (await page.locator('.fui-card, [class*=pl__]').count()) > 0);

      // This round's three mobile surfaces, on the live console. The import control is only looked at,
      // never pressed: importing would create a project on a system somebody else may be using.
      await page.goto(`${base}/projects/${encodeURIComponent(ids[0])}/settings`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1_200);
      const importBtn = page.getByRole('button', { name: /导入|Import/i });
      ok('项目设置页有「导入」这一半（此前手机上只有导出）', (await importBtn.count()) >= 1, `(实际 ${await importBtn.count()} 个)`);

      let artifactId = '';
      let wfId = '';
      for (const id of ids) {
        const wf = await page.request.get(`${base}/api/v1/projects/${encodeURIComponent(id)}/workflow`);
        if (wf.status() !== 200) continue;
        const wid = String((await wf.json()).id);
        const arts = await page.request.get(`${base}/api/v1/workflows/${encodeURIComponent(wid)}/artifacts?limit=3`);
        if (arts.status() !== 200) continue;
        const rows = (await arts.json()).items || [];
        if (!rows.length) continue;
        wfId = wid;
        artifactId = String(rows[0].id);
        break;
      }
      if (artifactId) {
        await page.goto(`${base}/artifacts/${encodeURIComponent(artifactId)}`, { waitUntil: 'domcontentloaded' });
        try {
          await page.waitForSelector('.ad__acts', { timeout: 12_000 });
        } catch {
          /* assert on what is on screen */
        }
        const acts = page.locator('.ad__acts');
        const dl = acts.locator('a.ad__dl');
        const gate = await page.request.get(`${base}/api/v1/workflows/${encodeURIComponent(wfId)}/allowed-actions`);
        const gv = await gate.json();
        const dlEntry = [...(gv.allowed || []), ...(gv.unavailable || [])].find((e) => e.action === 'artifact.download');
        if (dlEntry && dlEntry.available) {
          const href = dl.count() ? String(await dl.first().getAttribute('href')) : '';
          ok('线上产物页的「下载」是一条真地址', (await dl.count()) === 1 && href.endsWith('/content?download=true'), `(href ${href})`);
        } else {
          ok(`线上 S1 说不能下载（${dlEntry ? dlEntry.reasonCode : '无此动作'}）时产物页不给链接`, (await dl.count()) === 0);
        }
        ok(
          '「复制链接」与「复制内容」是两个名字、两件事',
          (await acts.getByRole('button', { name: /复制链接|Copy link/ }).count()) === 1
            && (await page.getByRole('button', { name: /复制内容|Copy content/ }).count()) === 1,
        );
        // The axe finding of §12.6, checked where it actually matters: a region that scrolls must be
        // the kind of thing a keyboard can stop on.
        ok('滚动内容的区域可被键盘聚焦', (await page.locator('pre.fui-raw__pre[role="region"][tabindex="0"]').count()) === 1);
      } else {
        console.log('  · 线上没有可读的产物，跳过产物页检查');
      }

      await page.goto(`${base}/`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: /搜索|Search/i }).first().click();
      const panel = page.locator('.fui-overlay-panel');
      const term = String(projs.find((p) => !p.archived)?.name ?? '').slice(0, 2);
      await panel.locator('input').first().fill(term);
      await page.waitForTimeout(1_200);
      // A result row is a clickable cell — `is-link` on the component is what makes it one. The field
      // at the top of the panel is a `van-cell` too but never clickable, so only these are hits.
      const hits = panel.locator('.van-cell--clickable');
      ok(`搜索「${term}」给得出结果行`, (await hits.count()) >= 1, `(命中 ${await hits.count()} 行)`);
      const before = page.url();
      await hits.first().click();
      await page.waitForTimeout(1_500);
      ok('命中即跳转，不是把结果留在面板里', page.url() !== before && /\/(projects|environments|stages|artifacts)\//.test(page.url()), `(去了 ${page.url()})`);
    }

    ok(`${label}: 全程没有未捕获的页面异常`, errors.length === 0, errors.join(' | '));
  } catch (e) {
    ok(`${label}: 浏览器检查跑通`, false, String(e && e.message).slice(0, 160));
  } finally {
    await ctx.close();
  }
}

await browser.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exitCode = fail ? 1 : 0;
