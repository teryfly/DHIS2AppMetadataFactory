/*
 * A `t('a.b')` naming a key that does not exist is not a missing translation — it is the key
 * itself, on screen, in the operator's language. Neither the en/zh parity check nor the
 * `zhCN: Messages` type can see it, because a hole in both locales is still a hole in both.
 */
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { en, zhCN } from './messages';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCOPES = ['components', 'features', 'lib', 'api', 'sse'];

/** `api/generated` is codegen output, not source with translatable strings in it. */
const SKIP = new Set(['generated']);

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

function leaf(obj: unknown, path: string[]): boolean {
  const value = path.reduce<unknown>(
    (acc, key) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[key] : undefined),
    obj,
  );
  return typeof value === 'string';
}

describe('translation keys used by the source', () => {
  it('names a key that exists, in both languages', () => {
    const missing: string[] = [];
    let scanned = 0;
    for (const scope of SCOPES) {
      for (const file of sources(join(SRC, scope))) {
        scanned += 1;
        const body = readFileSync(file, 'utf8');
        for (const match of body.matchAll(/(?:^|[^A-Za-z0-9_$.])t\(\s*'([a-z][\w]*(?:\.[\w]+)+)'/g)) {
          const key = String(match[1]);
          const path = key.split('.');
          if (!leaf(en, path) || !leaf(zhCN, path)) missing.push(`${file.slice(SRC.length + 1)}: ${key}`);
        }
      }
    }
    // a guard that reads nothing is the same as no guard
    expect(scanned).toBeGreaterThan(60);
    expect(missing).toEqual([]);
  });
});
