/**
 * UI-APP-002 · the first run in a Chinese browser has to be Chinese.
 *
 * The choice is read once, from `damf:language` then from the browser tag, and it drives i18next
 * *and* `Accept-Language`. What this guards is the failure where the two disagree: the lookup chain
 * collapsing to the fallback language renders every string in English while `<html lang>` and the
 * request header already say zh-CN, and an operator who never chose English sees an English console
 * on their very first screen.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

async function boot(language: string, stored: string | null): Promise<{ language: string; t: string }> {
  Object.defineProperty(globalThis, 'navigator', {
    value: { language, languages: [language] },
    configurable: true,
  });
  if (stored === null) globalThis.localStorage?.removeItem('damf:language');
  else globalThis.localStorage?.setItem('damf:language', stored);

  vi.resetModules();
  const i18n = (await import('./index')).default;
  await (await import('./index')).i18nReady;
  return { language: i18n.language, t: i18n.t('nav.setup') };
}

describe('boot language (UI-APP-002)', () => {
  beforeEach(() => globalThis.localStorage?.clear());

  it('a zh-CN browser with no stored choice renders Chinese', async () => {
    const view = await boot('zh-CN', null);
    expect(view.language).toBe('zh-CN');
    expect(view.t).toBe('开始使用');
    expect(document.documentElement.lang).toBe('zh-CN');
  });

  it('a bare zh tag is the same language, not an unknown one', async () => {
    expect((await boot('zh', null)).t).toBe('开始使用');
  });

  it('an English browser renders English', async () => {
    const view = await boot('en-US', null);
    expect(view.language).toBe('en');
    expect(view.t).toBe('Get started');
  });

  it('a stored choice outranks the browser', async () => {
    expect((await boot('en-US', 'zh-CN')).t).toBe('开始使用');
    expect((await boot('zh-CN', 'en')).t).toBe('Get started');
  });
});
