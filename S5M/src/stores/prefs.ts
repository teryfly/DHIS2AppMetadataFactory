import { defineStore } from 'pinia';
import { ref } from 'vue';
import { LS, readJSON, writeJSON, type Lang } from '@/lib/storage';

export type FontSize = 'system' | 'standard' | 'large' | 'xlarge';
export type ThemeMode = 'system' | 'light' | 'dark';

const FONT_SCALE: Record<FontSize, string> = {
  system: '',
  standard: '100%',
  large: '118%',
  xlarge: '137%',
};

function mediaDark(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export const usePrefsStore = defineStore('prefs', () => {
  const language = ref<Lang>(readJSON<Lang>(LS.language, 'zh-CN'));
  const font = ref<FontSize>(readJSON<FontSize>(LS.font, 'system'));
  const theme = ref<ThemeMode>(readJSON<ThemeMode>(LS.theme, 'system'));

  function effectiveDark(): boolean {
    return theme.value === 'dark' || (theme.value === 'system' && mediaDark());
  }

  function apply(): void {
    const root = document.documentElement;
    root.setAttribute('data-theme', effectiveDark() ? 'dark' : 'light');
    root.style.fontSize = FONT_SCALE[font.value];
    root.setAttribute('lang', language.value === 'en' ? 'en' : 'zh-CN');
  }

  function setLanguage(v: Lang): void {
    language.value = v;
    writeJSON(LS.language, v);
    apply();
  }

  function setFont(v: FontSize): void {
    font.value = v;
    writeJSON(LS.font, v);
    apply();
  }

  function setTheme(v: ThemeMode): void {
    theme.value = v;
    writeJSON(LS.theme, v);
    apply();
  }

  return { language, font, theme, apply, setLanguage, setFont, setTheme, effectiveDark };
});
