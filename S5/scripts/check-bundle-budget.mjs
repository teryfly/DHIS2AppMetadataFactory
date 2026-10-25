// UI-NFR-06 gate: "首屏 JavaScript（gzip）≤ 200 KB，单个功能块 ≤ 300 KB". Reads the built dist/ and
// fails when the first screen or a route chunk exceeds its budget. Run after `npm run build`.
//
// "First screen" is defined the only honest way: what index.html itself references — the entry
// module, every modulepreload (Vite hoists the static import graph into those) and every
// stylesheet — plus same-origin files outside /assets and any inline <style>/<script>. Anything
// reached only through a dynamic import() is a lazy chunk, which is what makes `src/routes.tsx`
// being lazy a checked property instead of a claim.
import { readFileSync, writeFileSync, readdirSync, mkdirSync, existsSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import budget from '../budget.config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const KB = 1024;
const KB2 = (n) => `${(n / KB).toFixed(1)} KB`;

function fail(msg) {
  console.error(`\n✗ bundle budget: ${msg}\n`);
  process.exitCode = 1;
}

if (!existsSync(join(dist, 'index.html'))) {
  fail('no dist/index.html — run `npm run build` first');
  process.exit(1);
}

const html = readFileSync(join(dist, 'index.html'), 'utf8');
const assetsDir = join(dist, 'assets');
const gzOf = (file) => gzipSync(readFileSync(join(assetsDir, file)), { level: 9 }).length;
const rawOf = (file) => readFileSync(join(assetsDir, file)).length;

const refs = [...html.matchAll(/(?:src|href)="\/assets\/([^"]+?)"/g)].map((m) => m[1]);
const isJs = (f) => f.endsWith('.js');
const firstScreenJs = refs.filter(isJs);
const firstScreenCss = refs.filter((f) => f.endsWith('.css'));

const extra = { js: 0, css: 0, files: [] };
for (const ref of [...html.matchAll(/(?:src|href)="(\/(?!assets\/)[^"]+?)"/g)].map((m) => m[1])) {
  const p = join(dist, ref);
  if (!existsSync(p)) continue;
  const gz = gzipSync(readFileSync(p), { level: 9 }).length;
  if (ref.endsWith('.js')) extra.js += gz;
  else if (ref.endsWith('.css')) extra.css += gz;
  else continue;
  extra.files.push({ file: ref, gzip: gz });
}
const inlineGz = (re) => [...html.matchAll(re)].reduce((sum, m) => sum + gzipSync(m[1], { level: 9 }).length, 0);
extra.js += inlineGz(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g);
extra.css += inlineGz(/<style[^>]*>([\s\S]*?)<\/style>/g);

const allJs = existsSync(assetsDir) ? readdirSync(assetsDir).filter(isJs) : [];
const lazyJs = allJs.filter((f) => !firstScreenJs.includes(f));

const rows = [];
const firstScreenJsTotal =
  firstScreenJs.reduce((sum, f) => {
    const gz = gzOf(f);
    rows.push({ file: f, kind: 'first-screen', raw: rawOf(f), gzip: gz, cap: null });
    return sum + gz;
  }, 0) + extra.js;
const firstScreenCssTotal =
  firstScreenCss.reduce((sum, f) => {
    const gz = gzOf(f);
    rows.push({ file: f, kind: 'first-screen-css', raw: rawOf(f), gzip: gz, cap: budget.firstScreenCssBytes });
    return sum + gz;
  }, 0) + extra.css;

const exempted = [];
for (const f of lazyJs) {
  const gz = gzOf(f);
  const hit = budget.exempt.find((e) => e.pattern.test(f));
  if (hit) {
    exempted.push({ file: f, gzip: gz, reason: hit.reason });
    rows.push({ file: f, kind: 'exempt', raw: rawOf(f), gzip: gz, cap: null });
    continue;
  }
  rows.push({ file: f, kind: 'lazy', raw: rawOf(f), gzip: gz, cap: budget.lazyChunkBytes });
  if (gz > budget.lazyChunkBytes) fail(`lazy chunk ${f} is ${KB2(gz)} — over the ${KB2(budget.lazyChunkBytes)} route cap`);
}

if (firstScreenJsTotal > budget.firstScreenJsBytes) {
  fail(`first-screen JS is ${KB2(firstScreenJsTotal)} gzip — over the ${KB2(budget.firstScreenJsBytes)} UI-NFR-06 cap`);
}
if (firstScreenCssTotal > budget.firstScreenCssBytes) {
  fail(`first-screen CSS is ${KB2(firstScreenCssTotal)} gzip — over the ${KB2(budget.firstScreenCssBytes)} ceiling`);
}

for (const f of extra.files) rows.push({ file: f.file, kind: 'first-screen-public', raw: null, gzip: f.gzip, cap: null });
if (extra.js || extra.css) {
  rows.push({ file: '(inline in index.html)', kind: 'first-screen-inline', raw: null, gzip: extra.js + extra.css, cap: null });
}

const outDir = join(root, 'docs', 'reports');
if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
writeFileSync(
  join(outDir, 'bundle-budget.json'),
  JSON.stringify(
    {
      generatedBy: 'scripts/check-bundle-budget.mjs',
      requirement: 'UI-NFR-06',
      budgets: {
        firstScreenJsBytes: budget.firstScreenJsBytes,
        lazyChunkBytes: budget.lazyChunkBytes,
        firstScreenCssBytes: budget.firstScreenCssBytes,
      },
      firstScreenJsBytes: firstScreenJsTotal,
      firstScreenCssBytes: firstScreenCssTotal,
      lazyChunkCount: lazyJs.length,
      exempted,
      rows,
      pass: process.exitCode !== 1,
    },
    null,
    2,
  ) + '\n',
);

writeFileSync(
  join(outDir, 'bundle-budget.md'),
  [
    '# 首屏与分块包体（UI-NFR-06）',
    '',
    '生成：`npm run check:budget`（gzip -9，读 dist/index.html 的入口 + modulepreload + stylesheet + 同源 public 文件 + 内联 <style>/<script>）。',
    '',
    '| 指标 | 实测 | 上限 | 判定 |',
    '| --- | --- | --- | --- |',
    `| 首屏 JS | ${KB2(firstScreenJsTotal)} | ${KB2(budget.firstScreenJsBytes)} | ${firstScreenJsTotal <= budget.firstScreenJsBytes ? '✓' : '✗'} |`,
    `| 首屏 CSS | ${KB2(firstScreenCssTotal)} | ${KB2(budget.firstScreenCssBytes)} | ${firstScreenCssTotal <= budget.firstScreenCssBytes ? '✓' : '✗'} |`,
    `| 懒加载块 | ${lazyJs.length} 个，最大 ${KB2(Math.max(0, ...lazyJs.map(gzOf)))} | ${KB2(budget.lazyChunkBytes)} | ${process.exitCode ? '✗' : '✓'} |`,
    '',
    '按名豁免（UI-NFR-06「编辑器、图库仅在需要时加载」的可审计转写）：',
    '',
    '| 块 | gzip | 豁免理由 |',
    '| --- | --- | --- |',
    ...(exempted.length ? exempted.map((e) => `| ${e.file} | ${KB2(e.gzip)} | ${e.reason} |`) : ['| （无） | — | — |']),
    '',
    '首屏明细：',
    '',
    '| 文件 | gzip |',
    '| --- | --- |',
    ...firstScreenJs.map((f) => `| ${f} | ${KB2(gzOf(f))} |`),
    '',
  ].join('\n'),
);

console.log(`\n首屏 JS ${KB2(firstScreenJsTotal)} / ${KB2(budget.firstScreenJsBytes)} · CSS ${KB2(firstScreenCssTotal)} · 懒加载块 ${lazyJs.length} 个（豁免 ${exempted.length}）`);
for (const e of exempted) console.log(`  豁免 ${e.file} ${KB2(e.gzip)} — ${e.reason}`);
if (!process.exitCode) console.log('✓ UI-NFR-06 达标\n');
