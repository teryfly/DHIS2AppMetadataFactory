import { createI18n } from 'vue-i18n';
import zhCN from './locales/zh-CN';
import en from './locales/en';
import { LS, readJSON, type Lang } from '@/lib/storage';

export const LOCALES = { 'zh-CN': zhCN, en } as const;

function initialLocale(): Lang {
  const stored = readJSON<Lang>(LS.language, '' as Lang);
  if (stored === 'en' || stored === 'zh-CN') return stored;
  if (typeof navigator !== 'undefined' && navigator.language?.toLowerCase().startsWith('zh')) return 'zh-CN';
  return 'zh-CN';
}

export const i18n = createI18n({
  legacy: false,
  locale: initialLocale(),
  fallbackLocale: 'en',
  globalInjection: true,
  messages: LOCALES,
});

export function setLocale(l: Lang): void {
  i18n.global.locale.value = l;
}

export function currentLocale(): Lang {
  return i18n.global.locale.value as Lang;
}

/** Map an S1 errorCode to a localized "what happened + what to do" string (UM-NET-002). */
export function translateErrorCode(
  code: string,
  serverMessage?: string,
  details?: Record<string, unknown>,
): string {
  // `details.requires` names the precondition S1 actually refused on, which is the advice worth
  // reading; the bare code is the fallback.
  const requires = details?.requires;
  if (typeof requires === 'string') {
    const specific = `errors.${code}_REQUIRES_${requires}`;
    if (i18n.global.te(specific)) return String(i18n.global.t(specific));
  }
  const key = `errors.${code}`;
  if (i18n.global.te(key)) return String(i18n.global.t(key));
  return serverMessage ?? code;
}
