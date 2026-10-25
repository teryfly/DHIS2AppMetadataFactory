// UM-NFR-04 / UM-NFR-01: Lighthouse on the mobile preset, against the built app.
//   npm run report:lighthouse
// The preview server must already be running (it is what Playwright's webServer starts, or do it
// by hand): `VITE_S1_PROXY=http://127.0.0.1:8787 npm run preview -- --port 4173 --strictPort`
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { cpus, loadavg, totalmem, freemem } from 'node:os';
import { execFileSync } from 'node:child_process';

const URL_TO_TEST = process.env.LH_URL ?? 'http://127.0.0.1:4173/';
const OUT_DIR = 'docs/reports';
const THRESHOLDS = { performance: 85, accessibility: 95, 'best-practices': 90 };
// The full Lighthouse JSON is hundreds of KB: kept out of git (see .gitignore), used only to
// derive the committed summary.
const RAW = join(process.env.TMPDIR ?? '/tmp', 's5m-lighthouse.json');

/** Newest Playwright Chromium; this host ships no system Chrome. */
function findChrome() {
  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const root = `${process.env.HOME ?? ''}/.cache/ms-playwright`;
  if (!existsSync(root)) return null;
  for (const dir of readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()) {
    const candidate = join(root, dir, 'chrome-linux64', 'chrome');
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

const chrome = findChrome();
if (!chrome) {
  console.error('no Chromium found — set CHROME_PATH. Failing loudly rather than reporting a 0 score.');
  process.exit(1);
}

// The CLI, not the Node API: Lighthouse 13 changed the programmatic connector signature and the
// error it produced looked like a browser problem. `--quiet --output json` is stable across
// versions, and the JSON is what the report below is derived from.
process.env.CHROME_PATH = chrome;
process.env.LIGHTHOUSE_CHROMIUM_PATH = chrome;

// Guard against the mistake this is easy to make: auditing a stale preview process, or the
// dev-harness build Playwright leaves in dist/ (it carries /dev/* routes a real deployment has no
// use for, and they inflate the first screen). Compare the entry the server answers with.
const diskEntry = readFileSync('dist/index.html', 'utf8').match(/assets\/(index-[^"]+\.js)/)?.[1];
const servedEntry = (await fetch(URL_TO_TEST).then((r) => r.text())).match(/assets\/(index-[^"]+\.js)/)?.[1];
if (!diskEntry || !servedEntry) {
  console.error('no entry asset in dist/index.html or the served page — is `npm run preview` up on 4173?');
  process.exit(1);
}
if (diskEntry !== servedEntry) {
  console.error(`served bundle is stale: server has ${servedEntry}, dist has ${diskEntry} — restart \`npm run preview\``);
  process.exit(1);
}

const args = [
  URL_TO_TEST,
  '--only-categories=performance,accessibility,best-practices',
  '--form-factor=mobile',
  '--screenEmulation.mobile',
  '--throttlingMethod=simulate',
  // Median of runs, not a single pull: on a shared box one measurement is dominated by whoever
  // else is using it (observed TBT 2,097 ms vs 29,302 ms for the same bundle).
  `--runs=${process.env.LH_RUNS ?? 3}`,
  '--chrome-flags=--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage',
  '--output=json',
  `--output-path=${RAW}`,
  '--quiet',
];

let lhr;
// Captured before the run: a load average above the core count means every timing number below is
// whoever else is on this machine, not the app. Recorded so the report cannot be read without it.
const hostBefore = { cores: cpus().length, load1: +loadavg()[0].toFixed(2), load5: +loadavg()[1].toFixed(2), freeMB: Math.round(freemem() / 1048576), totalMB: Math.round(totalmem() / 1048576) };
try {
  // A file, not a pipe: the JSON is megabytes and `maxBuffer`/EPIPE behaviour made this flaky.
  execFileSync('node', ['node_modules/lighthouse/cli/index.js', ...args], { stdio: 'inherit', env: process.env });
  lhr = JSON.parse(readFileSync(RAW, 'utf8'));
} catch (e) {
  console.error('lighthouse failed:', String(e.message ?? e).slice(0, 400));
  process.exit(1);
}

const cats = lhr.categories;
const scores = Object.fromEntries(Object.entries(cats).map(([k, v]) => [k, Math.round((v?.score ?? 0) * 100)]));
const a = lhr.audits;
const ms = (k) => Math.round(a[k]?.numericValue ?? 0);
const summary = {
  url: URL_TO_TEST,
  chrome,
  lighthouseVersion: lhr.lighthouseVersion,
  fetchTime: lhr.fetchTime,
  throttling: 'Lighthouse 内置 4G 模拟（1.6 Mbps / RTT 150 ms）',
  // Timing scores are only meaningful next to the machine they were taken on; the report carries
  // its own validity caveat rather than a bare number.
  host: {
    ...hostBefore,
    runs: Number(process.env.LH_RUNS ?? 3),
    // load1 > 2× cores has been observed to move TBT on the identical bundle from 2.1 s to 29.3 s.
    performanceAttestable: hostBefore.load1 <= hostBefore.cores * 1.5,
  },
  scores,
  thresholds: THRESHOLDS,
  metrics: {
    fcpMs: ms('first-contentful-paint'),
    lcpMs: ms('largest-contentful-paint'),
    tbtMs: ms('total-blocking-time'),
    cls: a['cumulative-layout-shift']?.numericValue ?? null,
  },
  lowScoringAudits: Object.values(a)
    .filter((x) => typeof x?.score === 'number' && x.score < 0.9)
    .map((x) => ({ id: x.id, score: x.score, title: x.title })),
};

mkdirSync(OUT_DIR, { recursive: true });
writeFileSync(join(OUT_DIR, 'lighthouse-mobile.json'), JSON.stringify(summary, null, 2) + '\n');
writeFileSync(
  join(OUT_DIR, 'lighthouse-mobile.md'),
  [
    '# Lighthouse 移动端（UM-NFR-04 / UM-NFR-01）',
    '',
    `生成：\`npm run report:lighthouse\`；目标 ${URL_TO_TEST}；Chrome \`${chrome}\`；Lighthouse ${summary.lighthouseVersion}；`,
    `节流 ${summary.throttling}；取 ${summary.host.runs} 次运行的中位数。`,
    `测量机：${summary.host.cores} 核，load1 ${summary.host.load1} / load5 ${summary.host.load5}，空闲内存 ${summary.host.freeMB} MB / ${summary.host.totalMB} MB。`,
    summary.host.performanceAttestable
      ? 'load1 在核数的 1.5 倍以内，计时类得分可采信。'
      : `load1 已达 ${summary.host.cores} 核的 ${(summary.host.load1 / summary.host.cores).toFixed(1)} 倍 —— **本机无法为性能分作证**（同一产物三次独立测量的 TBT 曾从 2.1 s 跳到 29.3 s），需在安静机器上重跑；无障碍与最佳实践是 DOM/CSS 审计，不受负载影响。`,
    '',
    '| 类别 | 得分 | 阈值 | 判定 |',
    '| --- | --- | --- | --- |',
    ...Object.entries(THRESHOLDS).map(([k, t]) => `| ${k} | ${scores[k]} | ≥ ${t} | ${scores[k] >= t ? 'PASS' : 'FAIL'} |`),
    '',
    `FCP ${summary.metrics.fcpMs} ms · LCP ${summary.metrics.lcpMs} ms · TBT ${summary.metrics.tbtMs} ms · CLS ${summary.metrics.cls}`,
    '',
    `UM-NFR-01 要求 4G 模拟下 FCP < 2 s、LCP < 2.5 s：${summary.metrics.fcpMs < 2000 && summary.metrics.lcpMs < 2500 ? 'PASS' : 'FAIL'}。`,
    '',
    '未达 0.9 的审计项（丢分来源）：',
    '',
    '| 审计 | 得分 | 标题 |',
    '| --- | --- | --- |',
    ...(summary.lowScoringAudits.length ? summary.lowScoringAudits.map((x) => `| ${x.id} | ${x.score} | ${x.title} |`) : ['| （无） | — | — |']),
    '',
  ].join('\n'),
);

// `LH_KEEP=1` leaves the raw LHR in tmp/ so the failing DOM nodes can be inspected.
if (!process.env.LH_KEEP) rmSync(RAW, { force: true });
else console.error(`raw LHR kept: ${RAW}`);

const failing = Object.entries(THRESHOLDS).filter(([k]) => scores[k] < THRESHOLDS[k]);
console.log(JSON.stringify({ scores, thresholds: THRESHOLDS, metrics: summary.metrics }, null, 1));
if (failing.length) {
  console.error(`below threshold: ${failing.map(([k, t]) => `${k} ${scores[k]}<${t}`).join(', ')}`);
  process.exitCode = 1;
}
