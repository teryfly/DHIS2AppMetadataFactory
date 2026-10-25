<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useOnline } from '@vueuse/core';
import { useAllowedActions, usePlatformActions } from '@/api/queries';
import { indexActions, resolveAvailability } from '@/api/actions';
import { useOperatorStore } from '@/stores/operator';
import FuiButton from './FuiButton.vue';

// Button availability comes ONLY from allowed-actions (UM-BR-01); a missing entry is treated as
// unavailable, never assumed (UM-BR-02). Also blocked without operator identity or while offline.
const props = withDefaults(
  defineProps<{
    action: string;
    targetId?: string | null;
    workflowId?: string;
    platform?: boolean;
    variant?: 'primary' | 'default' | 'danger' | 'warning' | 'ghost' | 'success';
    size?: 'normal' | 'small' | 'large' | 'mini';
    block?: boolean;
    round?: boolean;
    loading?: boolean;
    disabled?: boolean;
    label?: string;
  }>(),
  { platform: false, variant: 'default', size: 'normal', block: false, round: false, disabled: false, loading: false, targetId: null, workflowId: '', label: '' },
);

const emit = defineEmits<{ (e: 'click'): void; (e: 'blocked', reason: string): void }>();

const { t } = useI18n();
const online = useOnline();
const operator = useOperatorStore();

const wfView = useAllowedActions(computed(() => props.workflowId ?? ''));
const platformView = usePlatformActions();
const view = computed(() => (props.platform ? platformView.data.value : wfView.data.value));
const isLoading = computed(() => (props.platform ? platformView.isLoading.value : props.workflowId ? wfView.isLoading.value : false));

const avail = computed(() => resolveAvailability(indexActions(view.value), props.action, props.targetId));
const canUse = computed(() => avail.value.available && operator.hasOperator && online.value && !props.disabled);

const reason = computed(() => {
  if (!operator.hasOperator) return t('operator.requiredHint');
  if (!online.value) return t('offline.actionsDisabled');
  if (isLoading.value) return '';
  const code = avail.value.reasonCode;
  return code ? t(`actionReason.${code}`) : avail.value.unknown ? t('actionReason.unknown') : '';
});

function onClick(): void {
  if (canUse.value) emit('click');
  else if (reason.value) emit('blocked', reason.value);
}
</script>

<template>
  <div class="fui-ab">
    <FuiButton :variant="variant" :size="size" :block="block" :round="round" :loading="loading || isLoading" :disabled="!canUse" @click="onClick">
      <slot>{{ label }}</slot>
    </FuiButton>
    <div v-if="!canUse && reason" class="fui-ab__reason">{{ reason }}</div>
  </div>
</template>

<style scoped>
.fui-ab__reason { margin-top: 4px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
