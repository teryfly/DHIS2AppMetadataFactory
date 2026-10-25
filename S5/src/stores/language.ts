/*
 * UI-APP-002: the interface language (简体中文 / English) is a browser/UI preference, persisted
 * locally. It drives i18next AND the `Accept-Language` request header. Only two tags are sent to
 * S1 (`zh-CN` or `en`); anything else falls back to `en` server-side.
 */
import { create } from 'zustand';

export type UiLanguage = 'zh-CN' | 'en';

export const SUPPORTED_LANGUAGES: UiLanguage[] = ['zh-CN', 'en'];

const STORAGE_KEY = 'damf:language';

function initialLanguage(): UiLanguage {
  try {
    const stored = globalThis.localStorage?.getItem(STORAGE_KEY) as UiLanguage | null;
    if (stored && SUPPORTED_LANGUAGES.includes(stored)) return stored;
    const nav = globalThis.navigator?.language ?? 'en';
    return nav.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
  } catch {
    return 'en';
  }
}

interface LanguageState {
  language: UiLanguage;
  setLanguage: (lang: UiLanguage) => void;
}

export const useLanguageStore = create<LanguageState>((set) => ({
  language: initialLanguage(),
  setLanguage: (language) => {
    try {
      globalThis.localStorage?.setItem(STORAGE_KEY, language);
    } catch {
      /* storage may be unavailable; language still applies in-session */
    }
    set({ language });
  },
}));

/** Non-hook accessor for the API middleware (synchronous, outside React render). */
export function getAcceptLanguage(): string {
  return useLanguageStore.getState().language;
}
