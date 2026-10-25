import { describe, expect, it } from 'vitest';
import { filesUnder, matches, read } from './walk';

// UM-REV-005 / UM-IF-001 (acceptance UM-AC-20).
//
// "禁止提供试运行或任何向目标 DHIS2 实例发送请求的功能，也不得通过 S1 或其他途径代理此类请求."
// The app has no such feature. These tests turn that into a checked property rather than a claim:
// an audit of the code paths that could reach a target instance, plus a dependency allow-list so a
// later version bump cannot smuggle a try-it-capable viewer in.

const SRC = filesUnder('src', (p) => !p.startsWith('src/api/generated/') && !p.startsWith('src/test/') && /\.(ts|vue)$/.test(p));
// A narrower set for the outbound-primitive audit only: a spec that writes
// `vi.stubGlobal('fetch', …)` is describing the network in order to fake it, which is the opposite
// of reaching it. Every other check here still scans test files too, so a hard-coded host in a test
// is still caught.
const SHIPPED = SRC.filter((p) => !/\.test\.ts$/.test(p));
const PUBLIC = filesUnder('public');

describe('UM-AC-20 · 出网原语只存在于 S1 传输层', () => {
  const ALLOWED = ['src/api/http.ts', 'src/api/upload.ts', 'src/sse/transport.ts'];
  // Three shapes, because the obvious one is not the only way to reach the network:
  //   fetch( / fetch. / fetch=        — called, member-accessed, or assigned
  //   = fetch / , fetch )             — handed over as a value (`const f = fetch;` then `f(url)`),
  //                                     which the old pattern missed entirely
  //   ['fetch'] / "XMLHttpRequest"    — computed access, the string form of the same trick
  // This catches drift, not a determined author: a comment containing the word still matches, which
  // is an acceptable failure direction (an explanation asked for, not a hole papered over).
  const PRIMITIVES =
    /\b(fetch|XMLHttpRequest|EventSource|WebSocket|sendBeacon)\s*[.(=]|[=(,]\s*(fetch|XMLHttpRequest|EventSource|WebSocket)\b|['"](fetch|XMLHttpRequest|EventSource|WebSocket|sendBeacon)['"]/;

  const offenders = matches(SHIPPED, PRIMITIVES).filter((m) => !ALLOWED.includes(m.path));

  it('no outbound primitive outside http/upload/transport', () => {
    expect(offenders.map((o) => `${o.path}:${o.line} ${o.text}`)).toEqual([]);
  });

  it('the allow-list is still exactly what talks to S1', () => {
    // A file removed from the list would silently widen the audit; assert the list is honest.
    const users = [...new Set(matches(SHIPPED, PRIMITIVES).map((m) => m.path))].sort();
    expect(users).toEqual(ALLOWED);
  });
});

describe('UM-AC-20 · 无任何目标实例可达地址', () => {
  it('no absolute URL literal in src or public', () => {
    const hits = matches([...SRC, ...PUBLIC], /https?:\/\//);
    expect(hits.map((h) => `${h.path}:${h.line} ${h.text}`)).toEqual([]);
  });

  it('no DHIS2 host or DHIS2-version API path', () => {
    // A DHIS2 instance is reached at <host>/api/3x/... — a path shape this app must never build.
    const hits = matches(SRC, /dhis2\.(org|com|net|io)|\/api\/3\d*\//i);
    expect(hits.map((h) => `${h.path}:${h.line} ${h.text}`)).toEqual([]);
  });

  it('index.html and public/config.js reference nothing external', () => {
    const html = read('index.html');
    expect([...html.matchAll(/(?:src|href)="(https?:)?\/\//g)]).toEqual([]);
    for (const p of PUBLIC) {
      expect(matches([p], /https?:\/\//)).toEqual([]);
    }
  });
});

describe('UM-AC-20 · 不存在 try-it 词汇与代码路径', () => {
  const TRY_IT = /try.?it|playground|sendRequest|dryRun|apiConsole|\bcurl\b|试运行|试一试|试一下/i;
  const SCOPES = ['src/features/review', 'src/fui', 'src/i18n/locales'];

  it('no try-it vocabulary in review surfaces, FUI or locales', () => {
    const paths = SCOPES.flatMap((s) => filesUnder(s, (p) => /\.(ts|vue)$/.test(p)));
    const hits = matches(paths, TRY_IT);
    expect(hits.map((h) => `${h.path}:${h.line} ${h.text}`)).toEqual([]);
  });

  it('the API review surface cannot reach the connection test', () => {
    // POST /environments/{key}/test-connection is an environment-management action (UM-ADM-001),
    // not an interface dry run — but it must never appear on an /api route. See ADR-006.
    const reviewFiles = filesUnder('src/features/review', (p) => /\.(ts|vue)$/.test(p));
    const hits = matches(reviewFiles, /testConnection|useTestConnection|environments\/.*\/test-connection/);
    expect(hits).toEqual([]);
  });
});

describe('UM-AC-20 · 依赖清单', () => {
  const pkg = JSON.parse(read('package.json')) as { dependencies: Record<string, string>; devDependencies: Record<string, string> };
  const runtime = Object.keys(pkg.dependencies);

  it('no UI/HTTP library that could talk to a target instance', () => {
    const banned = /@dhis2\/|swagger-ui|openapi-ui|@scalar\/|redoc|^axios$|^whatwg-fetch$|^openapi-fetch$/;
    expect(runtime.filter((d) => banned.test(d))).toEqual([]);
  });

  it('no browser automation library as a runtime dependency', () => {
    expect(runtime.filter((d) => /playwright|cypress|puppeteer/.test(d))).toEqual([]);
  });
});
