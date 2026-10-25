/*
 * UI-APP-002: interface language 简体中文 / English, all copy from translation resources,
 * user-switchable, and the choice drives `Accept-Language`. Resources are the type-checked
 * objects in ./messages so a missing translation is a compile error, not a silent fallback.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { en, zhCN } from './messages';
import { useLanguageStore, type UiLanguage } from '@/stores/language';

export const DEFAULT_NAMESPACE = 'translation';

export const i18nReady = i18n
  .use(initReactI18next)
  .init({
    resources: {
      'zh-CN': { translation: zhCN },
      en: { translation: en },
    },
    // Keep the same two tags the client sends as Accept-Language.
    supportedLngs: ['zh-CN', 'en'],
    fallbackLng: 'en',
    load: 'currentOnly',
    interpolation: { escapeValue: false },
    // The store owns the choice: it reads `damf:language` first and then maps the browser's own tag
    // (`zh*` → `zh-CN`, anything else → `en`), which is also what `Accept-Language` says. Handing
    // detection to i18next instead needs `nonExplicitSupportedLngs`, and that flag quietly breaks the
    // exact tag: `language` becomes `zh-CN` while the lookup chain collapses to `['en']`, so every
    // string on a Chinese operator's first screen comes back English. One rule, in one place.
    lng: useLanguageStore.getState().language,
    // Serialise nested keys with a dot path; we reference them as `gate.approve`.
    keySeparator: '.',
    nsSeparator: false,
  });

function applyLanguage(lang: UiLanguage): void {
  if (i18n.language !== lang) void i18n.changeLanguage(lang);
}

useLanguageStore.subscribe((s) => applyLanguage(s.language));

void i18nReady.then(() => {
  applyLanguage(useLanguageStore.getState().language);
  document.documentElement.lang = i18n.language === 'zh-CN' ? 'zh-CN' : 'en';
});
i18n.on('languageChanged', (lng) => {
  document.documentElement.lang = lng === 'zh-CN' ? 'zh-CN' : 'en';
});
i18n.on('languageChanged', (lng) => {
  document.documentElement.lang = lng === 'zh-CN' ? 'zh-CN' : 'en';
});

/** Resolve a localised message for an S1 errorCode; falls back to the server message, then the code. */
export function translateErrorCode(code: string | undefined, serverMessage?: string): string {
  if (!code) return serverMessage ?? '';
  const key = `errors.${code}`;
  if (i18n.exists(key)) return i18n.t(key);
  return serverMessage ?? code;
}

export default i18n;
