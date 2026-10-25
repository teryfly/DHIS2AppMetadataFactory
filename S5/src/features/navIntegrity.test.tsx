/*
 * Every internal link in this console has to land on a route.
 *
 * The metadata screen's 决策 column linked at `/projects/:id/decisions` for a page that was never
 * built: the router's catch-all answered with the 404 screen, and no test noticed, because the link
 * had only ever been followed by a person. That is the failure this file exists for — a link and the
 * route table drifting silently — and it is why the check reads the source rather than one rendered
 * screen: from inside any single page the bug is invisible.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { GLOBAL_NAV, PROJECT_SECTIONS, PROJECT_SUBTABS } from '@/navigation/ia';
import { en, zhCN } from '@/i18n/messages';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
/** Where a screen can link. `api/` and `lib/` hold endpoint paths, not addresses to navigate to. */
const SCOPES = ['features', 'components'];

/**
 * The route table, rebuilt from the JSX in `routes.tsx` by indentation: a `<Route>` written deeper is
 * a child of the last one written shallower, which is how the file is laid out and how react-router
 * resolves it. A `<Route>` with no `path` is a wrapper (the session gate, the shell) and contributes
 * no segment of its own. The catch-all is dropped: it answers every address with the 404 screen, so
 * counting it as a page is the very mistake this guard is here to catch.
 */
function routePatterns(): string[] {
  const body = readFileSync(join(SRC, 'routes.tsx'), 'utf-8');
  const stack: Array<{ indent: number; seg: string }> = [];
  const found: string[] = [];
  for (const line of body.split('\n')) {
    const open = line.match(/<Route\s+([^>/][^>]*)/);
    if (!open) continue;
    const attrs = open[1] ?? '';
    const indent = line.search(/\S/);
    while (stack.length > 0 && (stack.at(-1)?.indent ?? -1) >= indent) stack.pop();
    const path = attrs.match(/path="([^"]*)"/)?.[1];
    const prefix = stack.map((entry) => entry.seg).filter((seg) => seg !== '').join('/');
    if (path === undefined) {
      if (!attrs.endsWith('/>')) stack.push({ indent, seg: '' });
      if (attrs.includes('index')) found.push(`/${prefix}`);
      continue;
    }
    found.push(path.startsWith('/') ? path : `/${prefix ? `${prefix}/` : ''}${path}`);
    if (!attrs.endsWith('/>')) stack.push({ indent, seg: path });
  }
  return [...new Set(found.filter((pattern) => !pattern.endsWith('*')))].map(trim);
}

/** `${…}` and a concatenated id both stand for exactly one segment; a query is the page's own business. */
function normalise(raw: string): string {
  return trim(raw.replace(/\$\{[^}]*\}/g, 'x').split('?')[0] ?? '');
}

function trim(value: string): string {
  // the root is one slash, not an empty string: `/` is a page, and dropping it would hide it
  return value.length > 1 ? value.replace(/\/$/, '') : value;
}

function linkTargets(): Array<{ file: string; target: string }> {
  const out: Array<{ file: string; target: string }> = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
        const body = readFileSync(full, 'utf8');
        const seen = new Set<string>();
        // the whole literal after a navigation call, so a template keeps every segment it has
        for (const match of body.matchAll(/(?:to=|push\(|navigate\(|replace\()\s*\{?\s*[`'"]([^`'"]*)[`'"]/g)) {
          const raw = String(match[1] ?? '');
          // a literal ending in a slash is the head of a concatenation (`'/runs/' + id`), which the
          // next loop reads with its extra segment; on its own it names a page that is not the one
          if (raw.startsWith('/') && !raw.startsWith('/api') && !(raw.length > 1 && raw.endsWith('/'))) seen.add(normalise(raw));
        }
        // …and the concatenation form: `'/runs/' + id` is one segment longer than its literal
        for (const match of body.matchAll(/[`'"](\/[^`'"]*\/)[`'"]\s*\+/g)) {
          const raw = String(match[1] ?? '');
          if (!raw.startsWith('/api')) seen.add(normalise(`${raw}x`));
        }
        for (const value of seen) out.push({ file: full.slice(SRC.length + 1), target: value });
      }
    }
  };
  for (const scope of SCOPES) walk(join(SRC, scope));
  return out;
}

function answers(pattern: string, target: string): boolean {
  const regex = new RegExp(`^${trim(pattern).split('/').map((seg) => (seg.startsWith(':') ? '[^/]+' : seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))).join('/')}$`);
  return regex.test(target);
}

describe('internal links against the route table (UI-IA-001)', () => {
  it('reads the routes it is checking against', () => {
    const patterns = routePatterns();
    // a guard that matched nothing is the same as no guard
    expect(patterns.length).toBeGreaterThan(25);
    expect(patterns).toContain('/projects/:projectId/gate/:gateId');
    expect(patterns).toContain('/governance/:section');
    expect(patterns).toContain('/projects/:projectId/decisions');
    expect(patterns).toContain('/approvals');
    expect(patterns).not.toContain('/*');
  });

  it('keeps every segment of a templated link, and rejects what has no page', () => {
    const patterns = routePatterns();
    const answered = (target: string): boolean => patterns.some((pattern) => answers(pattern, target));
    expect(answered('/projects/x/gate/y')).toBe(true);
    expect(answered(normalise('/projects/${projectId}/decisions'))).toBe(true);
    // the bug this file was written for, in both directions: a page like that one answers, a page
    // beside it does not — and a link truncated at its first `${…}` would answer anything
    expect(answered('/projects/x/decisions2')).toBe(false);
    expect(answered('/projects/x/gate/y/extra')).toBe(false);
    expect(answered('/nonsense')).toBe(false);
  });

  it('scans enough of the console to be worth running', () => {
    const targets = linkTargets();
    expect(targets.length).toBeGreaterThan(25);
    expect(targets.some(({ target }) => target.split('/').length > 2)).toBe(true);
  });

  it('links nothing that has no page', () => {
    const patterns = routePatterns();
    const missing = linkTargets()
      .filter(({ target }) => !patterns.some((pattern) => answers(pattern, target)))
      .map(({ file, target }) => `${file} → ${target}`);
    expect(missing).toEqual([]);
  });

  it('navigates the grouped sidebar and the five project sections only where a route answers', () => {
    const patterns = routePatterns();
    const answered = (target: string): boolean => patterns.some((pattern) => answers(pattern, target));

    const globalTargets = GLOBAL_NAV.flatMap((group) => group.items.map((item) => item.to));
    expect(globalTargets.length).toBe(8);
    expect(globalTargets.filter((target) => !answered(target))).toEqual([]);

    expect(PROJECT_SECTIONS.length).toBe(5);
    // A section segment that owns a dynamic child needs one sample segment after it.
    const sample = (segment: string): string =>
      segment === 'stages' || segment === 'gate' ? `/projects/x/${segment}/y` : `/projects/x/${segment}`;
    const projectTargets = PROJECT_SECTIONS.flatMap((section) => section.segments.map(sample));
    expect(projectTargets.filter((target) => !answered(target))).toEqual([]);

    expect(PROJECT_SUBTABS.design).toHaveLength(2);
    expect(PROJECT_SUBTABS.artifacts).toHaveLength(6);
    expect(PROJECT_SUBTABS.delivery).toHaveLength(3);
    expect(PROJECT_SUBTABS.manage).toHaveLength(5);
    const subtabTargets = Object.values(PROJECT_SUBTABS)
      .flat()
      .map((tab) => sample(tab.landing));
    expect(subtabTargets.filter((target) => !answered(target))).toEqual([]);

    const leaves = (value: unknown, prefix = ''): string[] => {
      if (typeof value !== 'object' || value === null) return [prefix];
      return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
        leaves(child, prefix ? `${prefix}.${key}` : key),
      );
    };
    const enKeys = new Set(leaves(en));
    const zhKeys = new Set(leaves(zhCN));
    const labelKeys = [
      ...GLOBAL_NAV.flatMap((group) => [group.labelKey, ...group.items.map((item) => item.labelKey)]),
      ...PROJECT_SECTIONS.flatMap((section) => [
        section.labelKey,
        ...(PROJECT_SUBTABS[section.key] ?? []).map((tab) => tab.labelKey),
      ]),
    ];
    expect(labelKeys.filter((key) => !enKeys.has(key) || !zhKeys.has(key))).toEqual([]);
  });
});
