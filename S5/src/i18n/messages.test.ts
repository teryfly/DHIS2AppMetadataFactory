/*
 * UI-AC-08: every string shown to the user must come from a translation resource with a counterpart
 * in BOTH languages. These tests assert the key sets match exactly, the error-code catalogue covers
 * every code S1 can return, and the S1 unavailability reason codes are all translated.
 */
import { describe, it, expect } from 'vitest';
import { en, zhCN } from './messages';

function keysOf(obj: unknown, prefix = ''): string[] {
  if (typeof obj !== 'object' || obj === null) return [prefix];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    typeof v === 'object' && v !== null ? keysOf(v, prefix ? `${prefix}.${k}` : k) : [prefix ? `${prefix}.${k}` : k],
  );
}

function leafValues(obj: unknown, prefix = ''): [string, string][] {
  if (typeof obj === 'string') return prefix ? [[prefix, obj]] : [];
  if (typeof obj !== 'object' || obj === null) return [];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) =>
    leafValues(v, prefix ? `${prefix}.${k}` : k),
  );
}

function leafAt(obj: unknown, path: string): string | undefined {
  const found = leafValues(obj).find(([p]) => p === path);
  return found?.[1];
}

describe('i18n resources', () => {
  it('has identical leaf-key sets in English and Chinese', () => {
    const enKeys = new Set(keysOf(en));
    const zhKeys = new Set(keysOf(zhCN));
    const missingZh = [...enKeys].filter((k) => !zhKeys.has(k));
    const missingEn = [...zhKeys].filter((k) => !enKeys.has(k));
    expect(missingZh).toEqual([]);
    expect(missingEn).toEqual([]);
  });

  it('every leaf string is non-empty in both locales', () => {
    const check = (o: unknown, path = ''): void => {
      if (typeof o === 'string') {
        expect(o.length, path).toBeGreaterThan(0);
        return;
      }
      if (o && typeof o === 'object') for (const [k, v] of Object.entries(o)) check(v, path ? `${path}.${k}` : k);
    };
    check(en);
    check(zhCN);
  });

  it('interpolates with i18next braces, never a bare {placeholder}', () => {
    // i18next only substitutes `{{name}}`; a single-brace `{name}` is returned to the screen
    // verbatim, which is how the top bar came to read "expires {at}".
    const offenders: string[] = [];
    const check = (o: unknown, path = ''): void => {
      if (typeof o === 'string') {
        if (/\{[a-zA-Z_][\w.]*\}/.test(o.replace(/\{\{[^}]*\}\}/g, ''))) offenders.push(path);
        return;
      }
      if (o && typeof o === 'object') for (const [k, v] of Object.entries(o)) check(v, path ? `${path}.${k}` : k);
    };
    check(en);
    check(zhCN);
    expect(offenders).toEqual([]);
  });

  it('covers the S1 error codes and unavailability reasons', () => {
    const errCodes = Object.keys(en.errors);
    // Spot-check codes that S1's middleware / i18n actually returns in an error envelope.
    for (const c of ['OPERATOR_REQUIRED', 'APPROVAL_INVALIDATED', 'UID_POOL_EXHAUSTED', 'BUDGET_EXCEEDED', 'POLICY_DENIED', 'NETWORK_ERROR']) {
      expect(errCodes, c).toContain(c);
      // present in Chinese too
      expect((zhCN.errors as Record<string, string>)[c], c).toBeTruthy();
    }
    const reasons = Object.keys(en.actionReason).filter((k) => k !== 'unknown');
    // WARNING_UNRESOLVED is an allowed-actions reason (not an HTTP error code) — assert it there.
    for (const r of ['GATE_NOT_APPROVED', 'HIGH_QUESTION_OPEN', 'WORKFLOW_PAUSED', 'ENVIRONMENT_LOCKED', 'DESIGN_LINE_INCOMPLETE', 'WARNING_UNRESOLVED', 'EXPORT_NOT_TAKEN']) {
      expect(reasons, r).toContain(r);
    }
  });

  it('interpolation placeholders are identical across locales', () => {
    // Every `{{name}}` in an English leaf must have its twin in the Chinese leaf of the same key,
    // and vice versa. `providersFake` losing `{{which}}` on one side alone is invisible to the
    // key-set tests above and leaves the surviving braces running down the screen.
    const offenders: string[] = [];
    for (const [path, value] of leafValues(en)) {
      const zhValue = leafAt(zhCN, path);
      const grab = (s: string) => [...s.matchAll(/\{\{([\w.]+)\}\}/g)].map((m) => m[1]).sort();
      const a = grab(value);
      const b = grab(zhValue ?? '');
      if (a.join(',') !== b.join(',')) offenders.push(`${path}: en[${a}] vs zh[${b}]`);
    }
    expect(offenders).toEqual([]);
  });

  it('carries no design-time vocabulary in any string the user reads', () => {
    // Subsystem codes, requirement IDs and milestone markers belong to the SRS and to code comments,
    // not to the screen: the operator's console has never contained a definition of "S3", and the
    // getting-started page is the first thing a new operator reads. `DHIS2` is a product name, so the
    // code pattern is bounded — an unbounded `S[1-4]` substring test reports it 60 times a file.
    const banned: [string, RegExp][] = [
      ['subsystem code', /(^|[^A-Za-z0-9_])S[0-5](M?|-M)(?![A-Za-z0-9_])/],
      ['requirement id', /(^|[^A-Za-z0-9_])(S5IF|CP|UI|UM|FR|NFR|AC|DX|BP|D)-([A-Z]+-)?\d{1,3}(?![A-Za-z0-9_])/],
      ['milestone marker', /(^|[^A-Za-z0-9_])M[1-5](?![A-Za-z0-9_])/],
      ['"子系统"', /子系统/],
      ['"subsystem"', /subsystem/i],
      // A project's own 里程碑 (DRAFT → VERIFIED) is business vocabulary and stays; the delivery
      // plan of the product is not something an operator can act on.
      ['milestone as a delivery plan', /current milestone|belong[s]? to a milestone|当前里程碑|尚未交付的里程碑|里程碑尚未|个里程碑/i],
      ['raw enum token in prose', /PROJECT_EXPORT|PHI_PII|\bRESTRICTED\b|\bDISCOVERY\b|DEPLOY_GATE/],
      ['library name', /Monaco/],
    ];
    const offenders: string[] = [];
    for (const locale of [['en', en], ['zh-CN', zhCN]] as const) {
      for (const [path, value] of leafValues(locale[1])) {
        for (const [label, pattern] of banned) {
          if (pattern.test(value)) offenders.push(`${locale[0]} · ${path} (${label}): ${value.slice(0, 90)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('writes the Chinese interface in Chinese product vocabulary', () => {
    // `S5M_移动优先前端_SRS.md` §2 fixes the on-screen words: 蓝图 / 登记表 / 审批节点 / 提示词 /
    // 修改提案 / 密钥-凭证引用. The English column keeps its own terms — `Gate` is right in English and
    // wrong in a Chinese sentence, where it reads as a name the operator was never taught.
    const untranslated = /(?<![A-Za-z])(Blueprints?|Registries|Gates?|Prompts?|Patches?|Secrets?)(?![A-Za-z])/;
    const offenders: string[] = [];
    for (const [path, value] of leafValues(zhCN)) {
      if (untranslated.test(value)) offenders.push(`${path}: ${value.slice(0, 80)}`);
    }
    expect(offenders).toEqual([]);
  });
});
