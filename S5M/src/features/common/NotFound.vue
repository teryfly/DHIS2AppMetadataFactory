<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';
import { FuiEmpty } from '@/fui';
const { t } = useI18n();
const router = useRouter();
</script>

<template>
  <div class="page">
    <!-- The action existed visually but nothing listened for it, so the one button on a dead-end
         page did nothing. `notImplemented.body` was also the wrong sentence: this is an unknown
         route, not a screen we admitted we had not built. -->
    <FuiEmpty :title="$t('common.notFound')" :action-text="$t('common.back')" @action="router.replace('/')">
      <p class="page__code">404</p>
      <p>{{ t('errors.NOT_FOUND') }}</p>
    </FuiEmpty>
  </div>
</template>

<style scoped>
.page { padding: var(--fui-sp-4); }
.page__code { font-size: var(--fui-fs-xl); color: var(--fui-text-tertiary); }
</style>
