import { describe, it, expect } from 'vitest';
import zh from './locales/zh-CN';
import en from './locales/en';
import { filesUnder, read } from '../test/walk';

function keySet(obj: unknown, prefix = ''): string[] {
  if (obj && typeof obj === 'object') {
    return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) => keySet(v, prefix ? `${prefix}.${k}` : k));
  }
  return [prefix];
}

function leafValues(obj: unknown, prefix = ''): [string, string][] {
  if (typeof obj === 'string') return prefix ? [[prefix, obj]] : [];
  if (obj && typeof obj === 'object') {
    return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) => leafValues(v, prefix ? `${prefix}.${k}` : k));
  }
  return [];
}

describe('i18n locale parity (UM-AC-16)', () => {
  it('zh-CN and en expose identical key sets', () => {
    const a = keySet(zh).sort();
    const b = keySet(en).sort();
    expect(a).toEqual(b);
  });

  it('every leaf value is a non-empty string', () => {
    const bad: string[] = [];
    const check = (obj: unknown, prefix = ''): void => {
      if (obj && typeof obj === 'object') for (const [k, v] of Object.entries(obj as Record<string, unknown>)) check(v, prefix ? `${prefix}.${k}` : k);
      else if (typeof obj !== 'string' || !obj.length) bad.push(prefix);
    };
    check(zh);
    check(en);
    expect(bad).toEqual([]);
  });

  it('all S1 error codes are translated', () => {
    const codes = ['OPERATOR_REQUIRED', 'APPROVAL_INVALIDATED', 'DRIFT_DETECTED', 'ENVIRONMENT_LOCKED', 'OUTSIDE_COMMIT_WINDOW', 'UID_POOL_EXHAUSTED', 'NETWORK_ERROR'];
    const z = zh as unknown as { errors: Record<string, string> };
    const e = en as unknown as { errors: Record<string, string> };
    for (const c of codes) {
      expect(z.errors[c]).toBeTruthy();
      expect(e.errors[c]).toBeTruthy();
    }
  });

  it('interpolation placeholders are identical across locales', () => {
    // vue-i18n interpolates `{name}` — a single brace, and nothing else. If one language drops the
    // placeholder while the other keeps it, the surviving braces print verbatim on the screen.
    const grab = (s: string) => [...s.matchAll(/(?<!\{)\{([A-Za-z_]\w*)\}(?!\})/g)].map((m) => m[1]).sort();
    const offenders: string[] = [];
    for (const [path, value] of leafValues(en)) {
      const zhValue = leafValues(zh).find(([p]) => p === path)?.[1] ?? '';
      const a = grab(value);
      const b = grab(zhValue);
      if (a.join(',') !== b.join(',')) offenders.push(`${path}: en[${a}] vs zh[${b}]`);
    }
    expect(offenders).toEqual([]);
  });

  it('carries no design-time vocabulary in any string the user reads', () => {
    // The subsystem codes and requirement IDs in these locales are the SRS's own numbering. On a
    // phone screen "S1 报告的子系统" is noise an implementer cannot act on, and `/start` is the
    // first thing they see. `DHIS2` is a product name whose tail reads as `S2`, hence the bounds.
    const banned: [string, RegExp][] = [
      ['subsystem code', /(^|[^A-Za-z0-9_])S[0-5](M?|-M)(?![A-Za-z0-9_])/],
      ['requirement id', /(^|[^A-Za-z0-9_])(S5IF|CP|UI|UM|FR|NFR|AC|DX|BP|D)-([A-Z]+-)?\d{1,3}(?![A-Za-z0-9_])/],
      ['milestone marker', /(^|[^A-Za-z0-9_])M[1-5](?![A-Za-z0-9_])/],
      ['"子系统"', /子系统/],
      ['"subsystem"', /subsystem/i],
      ['milestone as a delivery plan', /current milestone|当前里程碑|尚未交付的里程碑|里程碑尚未|个里程碑/i],
      ['raw enum token in prose', /PROJECT_EXPORT|PHI_PII|\bRESTRICTED\b|\bDISCOVERY\b|DEPLOY_GATE/],
      ['library name', /Monaco/],
    ];
    const offenders: string[] = [];
    for (const [name, locale] of [['zh-CN', zh], ['en', en]] as const) {
      for (const [path, value] of leafValues(locale)) {
        for (const [label, pattern] of banned) {
          if (pattern.test(value)) offenders.push(`${name} · ${path} (${label}): ${value.slice(0, 80)}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('writes the Chinese interface in Chinese product vocabulary', () => {
    // `S5M_移动优先前端_SRS.md` §2 fixes the on-screen words — 蓝图 / 登记表 / 审批节点 / 提示词 /
    // 修改提案 / 密钥-凭证引用 — and this is the clause that says 「界面统一使用中文术语」. The English
    // locale keeps its own terms: `Gate` is correct in English and a name nobody taught the operator.
    const untranslated = /(?<![A-Za-z])(Blueprints?|Registries|Gates?|Prompts?|Patches?|Secrets?)(?![A-Za-z])/;
    const offenders: string[] = [];
    for (const [path, value] of leafValues(zh)) {
      if (untranslated.test(value)) offenders.push(`${path}: ${value.slice(0, 80)}`);
    }
    expect(offenders).toEqual([]);
  });

  // A `t('a.b')` naming a key that exists in neither locale is invisible to the parity check above:
  // two identical holes still match. The operator sees the key itself on screen, so the third check
  // is against the source.
  it('every translation key used in shipped source exists in both locales', () => {
    const scopes = ['src/fui', 'src/features', 'src/layouts', 'src/lib', 'src/api', 'src/stores', 'src/sse', 'src/router'];
    const has = (obj: unknown, path: string[]): boolean =>
      path.reduce<unknown>((acc, k) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined), obj) !== undefined;
    const missing: string[] = [];
    for (const scope of scopes) {
      for (const file of filesUnder(scope, (p) => /\.(vue|ts)$/.test(p) && !/\.test\.ts$/.test(p) && !/\.fixture\.ts$/.test(p))) {
        const body = read(file);
        // `\$?t(` on purpose: inside a Vue template the global is `$t('…')`, and the character class
        // that keeps `.t(` (a method on something else) from matching used to swallow the `$` too —
        // which silently exempted every template call, 29 of them, from this check.
        for (const [, , key] of body.matchAll(/(?:^|[^A-Za-z0-9_.])(\$?t)\(\s*'([a-z][\w]*(?:\.[\w]+)+)'/g)) {
          const path = key.split('.');
          if (!has(zh, path) || !has(en, path)) missing.push(`${file}: ${key}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });
});
