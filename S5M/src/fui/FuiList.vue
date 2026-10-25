<script setup lang="ts">

import { List, PullRefresh } from 'vant';
import 'vant/es/list/style';
import 'vant/es/pull-refresh/style';
withDefaults(
  defineProps<{ loading?: boolean; finished?: boolean; error?: boolean; refreshing?: boolean; empty?: boolean; skeleton?: boolean }>(),
  { finished: false, error: false, refreshing: false, empty: false, skeleton: false },
);
const emit = defineEmits<{ (e: 'load'): void; (e: 'refresh'): void; (e: 'update:refreshing', v: boolean): void }>();
</script>

<!-- Pull-to-refresh + infinite scroll (UM-NAV-005), with skeletons instead of a page spinner. -->
<template>
  <PullRefresh :model-value="refreshing" @update:model-value="(v: boolean) => emit('update:refreshing', v)" @refresh="emit('refresh')">
    <div v-if="skeleton" class="fui-list__skeleton">
      <div v-for="n in 4" :key="n" class="fui-skel-row" />
    </div>
    <List v-else :loading="loading" :finished="finished" :error="error" :error-text="$t('errors.NETWORK_ERROR')" @load="emit('load')">
      <template #finished></template>
      <slot />
      <template #loading><span class="fui-list__loading">{{ $t('common.loading') }}</span></template>
    </List>
    <slot v-if="empty && !loading && !skeleton" name="empty" />
  </PullRefresh>
</template>

<style scoped>
.fui-list__loading { color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); }
.fui-list__skeleton { padding: var(--fui-sp-2) 0; }
.fui-skel-row { height: 72px; background: var(--fui-surface-2); border-radius: var(--fui-radius-card); margin-bottom: var(--fui-sp-3); animation: sk 1.4s infinite; }
@keyframes sk { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }
</style>
