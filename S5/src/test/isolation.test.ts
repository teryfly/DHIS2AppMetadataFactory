/*
 * UI-REV-004 / C-08: the API review surface is read-only, and that is a property a test holds open
 * rather than a comment someone will eventually delete.
 *
 * The Factory never sends a request to a target DHIS2 instance from the browser, and it never
 * proxies one through S1: DHIS2 access belongs to S4, behind approval and commit (DX-BR-01). Interface
 * debugging happens outside the Factory, with the development package.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { join } from 'node:path';

const ROOT = resolve(process.cwd());

function filesUnder(dir: string, test: (name: string) => boolean): string[] {
  const abs = join(ROOT, dir);
  const out: string[] = [];
  for (const entry of readdirSync(abs, { withFileTypes: true })) {
    const path = join(abs, entry.name);
    if (entry.isDirectory()) out.push(...filesUnder(join(dir, entry.name), test));
    else if (test(entry.name)) out.push(path);
  }
  return out;
}

const TRY_IT = /try.?it|playground|sendRequest|apiConsole|\bcurl\b|试运行|试一试/i;
const OUTBOUND = /\bfetch\s*\(|XMLHttpRequest|axios|new WebSocket|EventSource/;

function hits(files: string[], pattern: RegExp) {
  return files
    .flatMap((path) => readFileSync(path, 'utf8').split('\n').map((line, i) => ({ path: path.slice(ROOT.length + 1), line: i + 1, text: line })))
    .filter((row) => pattern.test(row.text));
}

describe('UI-REV-004 · the API review surface stays read-only', () => {
  const review = filesUnder('src/features/review', (n) => /\.(ts|tsx)$/.test(n));

  it('contains no interactive-request vocabulary', () => {
    expect(hits(review, TRY_IT).map((h) => `${h.path}:${h.line}`)).toEqual([]);
  });

  it('contains no outbound request primitive of its own', () => {
    // The console reaches S1 only through `api/client`; a second path out of the browser would be
    // both a CORS story and a way around the approval gate.
    const offenders = hits(review, OUTBOUND).filter((h) => !h.text.includes('from'));
    expect(offenders.map((h) => `${h.path}:${h.line} ${h.text.trim()}`)).toEqual([]);
  });

  it('never touches the environment connection test', () => {
    const found = hits(review, /testConnection|test-connection/);
    expect(found.map((h) => `${h.path}:${h.line}`)).toEqual([]);
  });
});
