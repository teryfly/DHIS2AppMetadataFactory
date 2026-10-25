import { describe, expect, it } from 'vitest';
import { filesUnder, read } from './walk';

// UM-AC-16: switching language must not require a reload, and the DOM must carry no hard-coded
// Chinese. The runtime half is tests/e2e/i18n.spec.ts; this is the compile-time half — a string
// written into a template can never translate itself, so it fails here instead of in the browser.
const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

const SCOPES = ['src/fui', 'src/features', 'src/layouts', 'src/lib', 'src/api', 'src/stores', 'src/sse', 'src/router'];

/**
 * Locales are the translations themselves; `src/features/dev` and `*.fixture.ts` never ship;
 * `*.test.ts` asserts on Chinese strings by design.
 */
function inScope(p: string): boolean {
  if (!/\.(vue|ts)$/.test(p)) return false;
  if (/\.test\.ts$/.test(p) || /\.fixture\.ts$/.test(p)) return false;
  if (p.startsWith('src/features/dev/') || p.startsWith('src/fui/internal/schemaFixtures')) return false;
  return true;
}

/** Comment-only text is allowed to be Chinese: it is for the reader of the code, not the user. */
function codeOnly(body: string): string[] {
  return body
    .split(/\r?\n/)
    .map((l) => l.replace(/<!--[\s\S]*?-->/g, '').replace(/\/\/.*$/, '').replace(/^\s*\*.*$/, ''))
    .filter((l) => !/^\s*(\/\/|\/\*|<!--)/.test(l));
}

// Time format patterns are the one legitimate exception: `fmtRelTime(iso, lang)` is a pure helper
// that renders both languages from an explicit argument (src/lib/time.test.ts asserts each arm),
// so it carries no untranslated string. The exception is pinned to that file — a component cannot
// claim the same idiom, because it would then be choosing the language itself instead of i18n.
const BILINGUAL = /lang === 'zh' \?/;
const BILINGUAL_FILES = new Set(['src/lib/time.ts']);

describe('UM-AC-16 · 生产源码不含硬编码中文', () => {
  const files = SCOPES.flatMap((s) => filesUnder(s, inScope));

  it('scans a non-trivial file set', () => {
    expect(files.length).toBeGreaterThan(80);
  });

  it('no CJK literal outside the locale files', () => {
    const hits: string[] = [];
    for (const p of files) {
      codeOnly(read(p)).forEach((line, i) => {
        if (!CJK.test(line)) return;
        if (BILINGUAL.test(line) && BILINGUAL_FILES.has(p)) return;
        hits.push(`${p}:${i + 1} ${line.trim().slice(0, 90)}`);
      });
    }
    expect(hits).toEqual([]);
  });
});
