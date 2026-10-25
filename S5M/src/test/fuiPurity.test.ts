import { describe, expect, it } from 'vitest';
import { filesUnder, matches, read } from './walk';

// UM-TECH-001 (页面只使用 FUI) and UM-TECH-002 (重型能力按路由懒加载).
//
// VAN_BACKLOG is a ratchet, not a tolerance: it holds the number of `van-*` sites left outside
// src/fui and only ever moves down. When it reaches 0 the strict assertions switch on and
// eslint.config.js flips FUI_SEVERITY to 'error' in the same commit.

// One site = one element to migrate (opening tag, `:deep(.van-*)` selector, or a `vant` import).
// Baseline at the start of this round: 87 (84 tags + 2 style leaks + 1 global import in main.ts);
// M2's M4 carve-out removed 6 of them. Ratchet down as files migrate; 0 switches on the strict
// assertions and flips eslint.config.js from warn to error in the same commit.
const VAN_BACKLOG: number = 0;

const SITE_RE = /<van-[a-z-]+|\.van-[a-z-]+|from ['"]vant['"]/g;

// The guard files are excluded: they contain the very patterns they forbid (`<van-x>` inside a
// lint message, a URL inside a regex), and a test that matches itself is not a test.
const OUTSIDE_FUI = filesUnder('src', (p) => /\.(ts|vue)$/.test(p) && !p.startsWith('src/fui/') && !p.startsWith('src/api/generated/') && !p.startsWith('src/test/'));
const FUI_TOP = filesUnder('src/fui', (p) => /\.(ts|vue)$/.test(p) && !p.startsWith('src/fui/internal/'));
const HEAVY = /codemirror|@codemirror|ajv|jsonc-parser|js-yaml|@antv\//;
const HEAVY_ALLOWED = ['src/fui/internal/', 'src/lib/json/', 'src/lib/openapi/', 'src/lib/graph/'];

/** Every occurrence (not line) of a Vant tag, Vant class selector, or bare `vant` import. */
function vanSites(paths: string[]): { path: string; line: number; text: string }[] {
  const out: { path: string; line: number; text: string }[] = [];
  for (const p of paths) {
    read(p)
      .split(/\r?\n/)
      .forEach((text, i) => {
        for (const m of text.matchAll(SITE_RE)) {
          out.push({ path: p, line: i + 1, text: m[0] });
        }
      });
  }
  return out;
}

describe('UM-TECH-001 · Vant 只在 FUI 层内', () => {
  const leaks = vanSites(OUTSIDE_FUI);

  it(`reports the remaining leak sites (ratchet ${VAN_BACKLOG})`, () => {
    expect(leaks.length).toBeLessThanOrEqual(VAN_BACKLOG);
  });
});

(VAN_BACKLOG === 0 ? describe : describe.skip)('UM-TECH-001 · 终态断言', () => {
  it('zero van- references outside src/fui', () => {
    expect(vanSites(OUTSIDE_FUI)).toEqual([]);
  });

  it('main.ts installs no Vant globally', () => {
    // Match the code, not prose: main.ts explains in a comment why there is no global install.
    expect(matches(['src/main.ts'], /from ['"]vant|app\.use\(\s*Vant\s*\)|vant\/lib/)).toEqual([]);
  });
});

describe('UM-TECH-001 · FUI 层自身的 Vant 引用必须显式导入', () => {
  // Removing `app.use(Vant)` from main.ts means a kebab `<van-x>` tag inside a FUI component only
  // resolves if that file imports the component itself. This is the one failure mode the de-global
  // migration introduces, so it is checked rather than trusted.
  const pascal = (tag: string) => tag.split('-').map((s) => s[0].toUpperCase() + s.slice(1)).join('');
  const FUI_FILES = filesUnder('src/fui', (p) => p.endsWith('.vue'));

  const offenders: string[] = [];
  for (const f of FUI_FILES) {
    const src = read(f);
    const template = src.match(/<template>[\s\S]*<\/template>/)?.[0] ?? '';
    const tags = [...new Set([...template.matchAll(/<van-([a-z-]+)/g)].map((m) => m[1]))];
    if (!tags.length) continue;
    // `import { Cell as VanCell }` binds the tag under either name.
    const imported = new Set(
      [...src.matchAll(/import\s*\{([^}]*)\}\s*from\s*'vant'/g)]
        .flatMap((m) => m[1].split(','))
        .flatMap((s) => s.split(/\s+as\s+/).map((n) => n.trim()))
        .filter(Boolean),
    );
    for (const tag of tags) {
      if (!imported.has(pascal(tag))) offenders.push(`${f}: <van-${tag}> without import { ${pascal(tag)} } from 'vant'`);
    }
  }

  it('every van- tag in a FUI template is imported in that file', () => {
    expect(offenders).toEqual([]);
  });

  it('each such component also pulls its own style', () => {
    const missing: string[] = [];
    for (const f of FUI_FILES) {
      const src = read(f);
      const template = src.match(/<template>[\s\S]*<\/template>/)?.[0] ?? '';
      for (const tag of new Set([...template.matchAll(/<van-([a-z-]+)/g)].map((m) => m[1]))) {
        // Radio and RadioGroup share a style directory; any match under that directory counts.
        const dir = tag.replace(/-group$/, '');
        if (!src.includes(`vant/es/${dir}/style`)) missing.push(`${f}: <van-${tag}> without 'vant/es/${dir}/style'`);
      }
    }
    expect(missing).toEqual([]);
  });
});

describe('UM-TECH-002 · 重型内核不进入首屏图', () => {
  const reachables = filesUnder('src', (p) => /\.(ts|vue)$/.test(p) && !HEAVY_ALLOWED.some((a) => p.startsWith(a)));

  it('no heavy library imported from outside the lazy cores and their private modules', () => {
    expect(matches(reachables, new RegExp(`^\\s*(import|export)[^\\n]*from ['"](${HEAVY.source})`, 'i')).map((m) => `${m.path}:${m.line}`)).toEqual([]);
  });

  it('FUI shells stay light: a barrel-visible component never imports a heavy kernel statically', () => {
    expect(matches(FUI_TOP, /from ['"][^'"]*(codemirror|ajv|jsonc-parser|js-yaml|@antv)/i).map((m) => `${m.path}:${m.line}`)).toEqual([]);
  });
});
