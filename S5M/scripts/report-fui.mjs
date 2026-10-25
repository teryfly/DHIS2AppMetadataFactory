// UM-TECH-001 burn-down report: which files still reference Vant outside src/fui.
import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SITE_RE = /<van-[a-z-]+|\.van-[a-z-]+|from ['"]vant['"]/g;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name);
    if (statSync(abs).isDirectory()) {
      if (['node_modules', 'dist', 'generated', '.git'].includes(name)) continue;
      walk(abs, out);
    } else if (/\.(ts|vue)$/.test(name) && !name.endsWith('.test.ts')) out.push(abs);
  }
  return out;
}

const files = walk(join(root, 'src')).filter((f) => !relative(root, f).startsWith('src/fui' + '/'));
const rows = [];
let total = 0;
for (const f of files) {
  const rel = relative(root, f).split('\\').join('/');
  let n = 0;
  for (const line of readFileSync(f, 'utf8').split(/\r?\n/)) n += (line.match(SITE_RE) ?? []).length;
  if (n) {
    rows.push({ file: rel, sites: n });
    total += n;
  }
}
rows.sort((a, b) => b.sites - a.sites || a.file.localeCompare(b.file));

const outDir = join(root, 'docs', 'reports');
if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });

const md = [
  '# UM-TECH-001 清偿进度（页面层 Vant 泄漏）',
  '',
  '生成：`npm run report:fui`。计数口径：`src/fui` 之外的每一处 `<van-*>` 标签、`:deep(.van-*)` 选择器或 `vant` 导入算一处；',
  '闭合标签与开标签是同一处，不重复计数。`src/fui` 内部引用 Vant 是设计允许的（FUI 就是那层包装）。',
  '',
  `**当前剩余：${total} 处**`,
  '',
  total ? '| 文件 | 剩余 |' : '（已全部清偿）',
  ...(total ? ['| --- | --- |', ...rows.map((r) => `| \`${r.file}\` | ${r.sites} |`)] : []),
  '',
  '防线：`eslint.config.js`（`vue/no-restricted-syntax` + `no-restricted-imports`，归零后由 warn 翻 error）',
  '与 `src/test/fuiPurity.test.ts` 的 ratchet 常数。',
  '',
].join('\n');

writeFileSync(join(outDir, 'fui-purity.md'), md);
writeFileSync(join(outDir, 'fui-purity.json'), JSON.stringify({ total, rows }, null, 2) + '\n');
console.log(`van- 泄漏剩余 ${total} 处，分布于 ${rows.length} 个文件 → docs/reports/fui-purity.md`);
