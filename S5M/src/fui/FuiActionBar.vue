<script setup lang="ts">
import { computed } from 'vue';
import { useOnline } from '@vueuse/core';
import { useOperatorStore } from '@/stores/operator';
import { Icon } from 'vant';
import 'vant/es/icon/style';

withDefaults(defineProps<{ moreDisabled?: boolean }>(), { moreDisabled: false });

const online = useOnline();
const operator = useOperatorStore();
// Without operator identity or while offline, all state-changing actions are disabled (UM-ME-001,
// UM-NET-005) — the bar itself still renders, with a one-tap hint to fill in the name.
const blocked = computed(() => !online.value || !operator.hasOperator);
</script>

<template>
  <div class="fui-action-bar">
    <div v-if="!operator.hasOperator" class="fui-action-bar__hint">
      <Icon name="warning-o" /> {{ $t('operator.requiredHint') }}
      <router-link to="/me" class="fui-action-bar__link">{{ $t('operator.requiredTitle') }}</router-link>
    </div>
    <div class="fui-action-bar__row">
      <slot name="more">
        <slot />
      </slot>
      <div class="fui-action-bar__primary"><slot name="primary" :blocked="blocked" /></div>
    </div>
  </div>
</template>

<style scoped>
.fui-action-bar { position: sticky; bottom: 0; left: 0; right: 0; background: var(--fui-surface); border-top: 1px solid var(--fui-border); padding: var(--fui-sp-2) var(--fui-sp-4) calc(var(--fui-sp-2) + var(--fui-safe-bottom)); z-index: 20; box-shadow: var(--fui-shadow-3); }
.fui-action-bar__row { display: flex; gap: var(--fui-sp-2); align-items: center; }
.fui-action-bar__primary { flex: 1; }
.fui-action-bar__primary :deep(.van-button) { width: 100%; }
.fui-action-bar__hint { display: flex; gap: 6px; align-items: center; color: var(--fui-warning); font-size: var(--fui-fs-sm); margin-bottom: 8px; }
.fui-action-bar__link { margin-left: auto; color: var(--fui-primary); text-decoration: none; font-weight: 600; }
</style>
