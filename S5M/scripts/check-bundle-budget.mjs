// UM-NFR-02 gate. Reads the built dist/ and fails when the first screen or a lazy chunk exceeds its
// budget. Run after `npm run build`; wired into `npm run verify`.
//
// "First screen" is defined the only honest way: what index.html itself references — the entry
// module, every modulepreload (Vite hoists the static import graph into these), and every
// stylesheet. Anything reached only by a dynamic import() is a lazy chunk.
import { readFileSync, writeFileSync, readdirSync, mkdirSync, existsSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import budget from '../budget.config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const KB = 1024;

function fail(msg) {
  console.error(`\n✗ bundle budget: ${msg}\n`);
  process.exitCode = 1;
}

if (!existsSync(join(dist, 'index.html'))) {
  fail(`no dist/index.html — run \`npm run build\` first`);
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

// Everything else index.html pulls before the app can paint: same-origin files outside /assets
// (public/config.js) and inline <style>/<script> blocks (the pre-JS skeleton). Counting them is
// what keeps the gate honest when the shell grows inline markup.
const extraBytes = { js: 0, css: 0, files: [] };
for (const ref of [...html.matchAll(/(?:src|href)="(\/(?!assets\/)[^"]+?)"/g)].map((m) => m[1])) {
  const p = join(dist, ref);
  if (!existsSync(p)) continue;
  const gz = gzipSync(readFileSync(p), { level: 9 }).length;
  if (ref.endsWith('.js')) extraBytes.js += gz;
  else if (ref.endsWith('.css')) extraBytes.css += gz;
  else continue;
  extraBytes.files.push({ file: ref, gzip: gz });
}
const inlineGz = (re) => [...html.matchAll(re)].reduce((s, m) => s + gzipSync(m[1], { level: 9 }).length, 0);
extraBytes.js += inlineGz(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g);
extraBytes.css += inlineGz(/<style[^>]*>([\s\S]*?)<\/style>/g);

const allJs = existsSync(assetsDir) ? readdirSync(assetsDir).filter(isJs) : [];
const lazyJs = allJs.filter((f) => !firstScreenJs.includes(f));

const rows = [];
const firstScreenTotal = firstScreenJs.reduce((s, f) => {
  const gz = gzOf(f);
  rows.push({ file: f, kind: 'first-screen', raw: rawOf(f), gzip: gz, cap: null });
  return s + gz;
}, 0);
const cssTotal = firstScreenCss.reduce((s, f) => {
  const gz = gzOf(f);
  rows.push({ file: f, kind: 'first-screen-css', raw: rawOf(f), gzip: gz, cap: budget.firstScreenCssBytes });
  return s + gz;
}, 0);

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

const firstScreenJsTotal = firstScreenTotal + extraBytes.js;
const firstScreenCssTotal = cssTotal + extraBytes.css;
for (const f of extraBytes.files) rows.push({ file: f.file, kind: 'first-screen-public', raw: null, gzip: f.gzip, cap: null });
if (extraBytes.js || extraBytes.css) {
  rows.push({ file: '(inline in index.html)', kind: 'first-screen-inline', raw: null, gzip: extraBytes.js + extraBytes.css, cap: null });
}

if (firstScreenJsTotal > budget.firstScreenJsBytes) {
  fail(`first-screen JS is ${KB2(firstScreenJsTotal)} gzip — over the ${KB2(budget.firstScreenJsBytes)} UM-NFR-02 cap`);
}
if (firstScreenCssTotal > budget.firstScreenCssBytes) {
  fail(`first-screen CSS is ${KB2(firstScreenCssTotal)} gzip — over the ${KB2(budget.firstScreenCssBytes)} FCP ceiling`);
}

function KB2(n) {
  return `${(n / KB).toFixed(1)} KB`;
}

const summary = {
  generatedBy: 'scripts/check-bundle-budget.mjs',
  budgets: {
    firstScreenJsBytes: budget.firstScreenJsBytes,
    lazyChunkBytes: budget.lazyChunkBytes,
    firstScreenCssBytes: budget.firstScreenCssBytes,
  },
  firstScreenJsBytes: firstScreenJsTotal,
  firstScreenCssBytes: firstScreenCssTotal,
  firstScreenInlineJsBytes: extraBytes.js,
  firstScreenInlineCssBytes: extraBytes.css,
  lazyChunkCount: lazyJs.length,
  exempted,
  rows,
  pass: process.exitCode !== 1,
};

const outDir = join(root, 'docs', 'reports');
if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, 'bundle-budget.json'), JSON.stringify(summary, null, 2) + '\n');

const md = [
  '# 首屏与分块包体（UM-NFR-02）',
  '',
  `生成：\`npm run check:budget\`（gzip -9，读 dist/index.html 的入口 + modulepreload + stylesheet + 同源 public 文件 + 内联 <style>/<script>）。`,
  '',
  `| 指标 | 实测 | 上限 | 判定 |`,
  `| --- | --- | --- | --- |`,
  `| 首屏 JS | ${KB2(firstScreenJsTotal)} | ${KB2(budget.firstScreenJsBytes)} | ${firstScreenJsTotal <= budget.firstScreenJsBytes ? '✓' : '✗'} |`,
  `| 首屏 CSS | ${KB2(firstScreenCssTotal)} | ${KB2(budget.firstScreenCssBytes)}（FCP 护栏） | ${firstScreenCssTotal <= budget.firstScreenCssBytes ? '✓' : '✗'} |`,
  `| 懒加载块 | ${lazyJs.length} 个，最大 ${KB2(Math.max(0, ...lazyJs.map(gzOf)))} | ${KB2(budget.lazyChunkBytes)} | ${process.exitCode ? '✗' : '✓'} |`,
  '',
  '按名豁免（UM-NFR-02「CodeMirror、G6、Scalar 除外」的可审计转写）：',
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
].join('\n');
writeFileSync(join(outDir, 'bundle-budget.md'), md);

console.log(`\n首屏 JS ${KB2(firstScreenJsTotal)} / ${KB2(budget.firstScreenJsBytes)} · CSS ${KB2(firstScreenCssTotal)} · 懒加载块 ${lazyJs.length} 个（豁免 ${exempted.length}）`);
for (const e of exempted) console.log(`  豁免 ${e.file} ${KB2(e.gzip)} — ${e.reason}`);
if (!process.exitCode) console.log('✓ UM-NFR-02 达标\n');
