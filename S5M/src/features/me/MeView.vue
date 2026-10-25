<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiButton, FuiCard, FuiCell, FuiField, FuiRadio, FuiRadioGroup } from '@/fui';
import { useOperatorStore } from '@/stores/operator';
import { useAuthStore } from '@/stores/auth';
import { usePrefsStore, type FontSize, type ThemeMode } from '@/stores/prefs';
import { useUiStore } from '@/stores/ui';
import { setLocale } from '@/i18n';
import { pushSuccess, pushError } from '@/lib/toast';
import { copyText, currentUrl } from '@/lib/clipboard';
import type { Lang } from '@/lib/storage';

const { t } = useI18n();
const router = useRouter();
const operator = useOperatorStore();
const auth = useAuthStore();
const prefs = usePrefsStore();
const ui = useUiStore();

const nameInput = ref(operator.name);
// An emptied box must not overwrite a stored name (UM-BR-04: the operator identity is what every
// write is attributed to).
watch(nameInput, (v) => {
  const next = v.trim();
  if (next) operator.setName(next);
});

function setLang(l: Lang): void {
  prefs.setLanguage(l);
  setLocale(l);
}

async function copyLink(): Promise<void> {
  // UM-P-06 asked for "copy this page's link"; the button was here with an empty handler.
  if (await copyText(currentUrl())) pushSuccess(t('common.copied'));
  else pushError(new Error(t('common.copyFailed')));
}

const fonts: FontSize[] = ['system', 'standard', 'large', 'xlarge'];
const fontLabels: Record<FontSize, string> = { system: 'me.fontSystem', standard: 'me.fontStandard', large: 'me.fontLarge', xlarge: 'me.fontXlarge' };
const themes: ThemeMode[] = ['system', 'light', 'dark'];
const themeLabels: Record<ThemeMode, string> = { system: 'me.themeSystem', light: 'me.themeLight', dark: 'me.themeDark' };

function clearCache(): void {
  ui.clearLocal();
  pushSuccess(t('me.clearCacheDone'));
}
</script>

<template>
  <div class="me">
    <FuiCard :title="t('me.operator')">
      <!-- With a session the account is the operator; a name typed here would never reach S1. -->
      <p v-if="auth.authenticated" class="me__hint">{{ t('auth.loggedInAs', { name: auth.session?.displayName || auth.subject }) }}</p>
      <template v-else>
        <FuiField v-model="nameInput" :placeholder="t('operator.placeholder')" />
        <p class="me__hint">{{ t('operator.why') }}</p>
      </template>
    </FuiCard>

    <FuiCard :title="t('me.language')">
      <FuiRadioGroup :model-value="prefs.language" direction="horizontal" @update:model-value="(v: Lang) => setLang(v)">
        <FuiRadio name="zh-CN">{{ t('me.langZh') }}</FuiRadio>
        <FuiRadio name="en">{{ t('me.langEn') }}</FuiRadio>
      </FuiRadioGroup>
    </FuiCard>

    <FuiCard :title="t('me.font')">
      <FuiRadioGroup :model-value="prefs.font" direction="horizontal" @update:model-value="(v: FontSize) => prefs.setFont(v)">
        <FuiRadio v-for="f in fonts" :key="f" :name="f">{{ t(fontLabels[f]) }}</FuiRadio>
      </FuiRadioGroup>
    </FuiCard>

    <FuiCard :title="t('me.theme')">
      <FuiRadioGroup :model-value="prefs.theme" direction="horizontal" @update:model-value="(v: ThemeMode) => prefs.setTheme(v)">
        <FuiRadio v-for="th in themes" :key="th" :name="th">{{ t(themeLabels[th]) }}</FuiRadio>
      </FuiRadioGroup>
    </FuiCard>

    <FuiCard :title="t('common.details')">
      <FuiCell :title="t('start.title')" is-link @click="router.push('/start')" />
      <FuiCell :title="t('me.envs')" is-link @click="router.push('/environments')" />
      <FuiCell :title="t('me.gov')" is-link @click="router.push('/governance')" />
      <FuiCell :title="t('access.title')" is-link @click="router.push('/access')" />
      <FuiCell :title="t('me.sysSettings')" is-link @click="router.push('/settings')" />
      <FuiCell :title="t('me.clearCache')" is-link @click="clearCache" />
    </FuiCard>

    <FuiCard :title="t('me.about')">
      <p class="me__hint">{{ t('me.aboutDesc') }}</p>
      <FuiButton size="small" :label="t('common.copyLink')" @click="copyLink" />
    </FuiCard>
  </div>
</template>

<style scoped>
.me { padding: var(--fui-sp-4); }
.me__hint { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 8px 0 0; }
</style>
