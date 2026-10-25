/*
 * S5M counterpart of the desktop guard: every address this console navigates to has to be a route.
 *
 * The two consoles share no code, so the same drift happens on either side on its own — and on a
 * phone a dead end is worse, because there is no address bar to read back what was asked for. The
 * mobile router is a flat array, so the table is read straight off it.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCOPES = ['features', 'layouts'];
/** Two modules outside the screens also build addresses: the memory bar and the search panel. */
const EXTRA = ['stores/ui.ts', 'lib/search.ts'];

function routePatterns(): string[] {
  const body = readFileSync(join(SRC, 'router/index.ts'), 'utf8');
  // the catch-all is dropped on purpose: it answers every address with the 404 screen, so counting it
  // as a page is exactly the mistake this guard exists to catch
  return [...new Set([...body.matchAll(/path: '([^']+)'/g).map((m) => String(m[1]))])]
    .filter((path) => !path.includes('pathMatch'))
    .map(trim);
}

function trim(value: string): string {
  // the root is one slash, not an empty string
  return value.length > 1 ? value.replace(/\/$/, '') : value;
}

/** `${…}` and a concatenated id both stand for exactly one segment; a query is the page's own business. */
function normalise(raw: string): string {
  return trim(raw.replace(/\$\{[^}]*\}/g, 'x').split('?')[0]);
}

function linkTargets(): Array<{ file: string; target: string }> {
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(ts|vue)$/.test(entry) && !/\.test\.(ts|vue)$/.test(entry)) files.push(full);
    }
  };
  for (const scope of SCOPES) walk(join(SRC, scope));
  for (const extra of EXTRA) files.push(join(SRC, extra));

  const out: Array<{ file: string; target: string }> = [];
  for (const file of files) {
    const body = readFileSync(file, 'utf8');
    const seen = new Set<string>();
    // the whole literal after a navigation call or a link target, so a template keeps every segment
    for (const match of body.matchAll(/(?:to=|push\(|navigate\(|replace\()\s*\{?\s*[`'"]([^`'"]*)[`'"]/g)) {
      const raw = String(match[1] ?? '');
      // a literal ending in a slash is the head of a concatenation (`'/runs/' + id`), which the next
      // loop reads with its extra segment; on its own it would name a page that is not the one
      if (raw.startsWith('/') && !raw.startsWith('/api') && !(raw.length > 1 && raw.endsWith('/'))) seen.add(normalise(raw));
    }
    // …and the concatenation form: `'/runs/' + id` is one segment longer than its literal
    for (const match of body.matchAll(/[`'"](\/[^`'"]*\/)[`'"]\s*\+/g)) {
      const raw = String(match[1] ?? '');
      if (!raw.startsWith('/api')) seen.add(normalise(`${raw}x`));
    }
    // the tab bar and the side nav keep their addresses in `to:` entries
    for (const match of body.matchAll(/\bto: '([^']*)'/g)) {
      const raw = String(match[1] ?? '');
      if (raw.startsWith('/') && !raw.startsWith('/api')) seen.add(normalise(raw));
    }
    for (const value of seen) out.push({ file: file.slice(SRC.length + 1), target: value });
  }
  return out;
}

function answers(pattern: string, target: string): boolean {
  const regex = new RegExp(`^${trim(pattern).split('/').map((seg) => (seg.startsWith(':') ? '[^/]+' : seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('/')}$`);
  return regex.test(target);
}

describe('internal links against the route table (UM-NAV-001)', () => {
  it('reads the routes it is checking against', () => {
    const patterns = routePatterns();
    expect(patterns.length).toBeGreaterThan(25);
    expect(patterns).toContain('/governance/:section');
    expect(patterns).toContain('/gates/:id');
    expect(patterns).toContain('/');
  });

  it('keeps every segment of a templated link, and rejects what has no page', () => {
    const patterns = routePatterns();
    const answered = (target: string): boolean => patterns.some((pattern) => answers(pattern, target));
    expect(answered('/projects/x/members')).toBe(true);
    expect(answered(normalise('/projects/${projectId}/inputs/${row.artifactId}/column-mapping'))).toBe(true);
    // a link truncated at its first `${…}` would answer anything, which is how a guard like this
    // passes while the console 404s
    expect(answered('/projects/x/gates/y')).toBe(false);
    expect(answered('/nonsense')).toBe(false);
  });

  it('scans enough of the console to be worth running', () => {
    const targets = linkTargets();
    expect(targets.length).toBeGreaterThan(25);
    expect(targets.some(({ target }) => target.split('/').length > 3)).toBe(true);
  });

  it('links nothing that has no page', () => {
    const patterns = routePatterns();
    const missing = linkTargets()
      .filter(({ target }) => !patterns.some((pattern) => answers(pattern, target)))
      .map(({ file, target }) => `${file} → ${target}`);
    expect(missing).toEqual([]);
  });
});
