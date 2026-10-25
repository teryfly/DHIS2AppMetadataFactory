<script setup lang="ts">
import FuiSkeleton from './FuiSkeleton.vue';
import FuiEmpty from './FuiEmpty.vue';
import FuiGuidedEmpty from './FuiGuidedEmpty.vue';
import type { ReadIntent } from '@/api/notGenerated';

// The single loading/error/empty shell every page uses. `intent` (src/api/notGenerated) upgrades
// empty and 404 to the UM-REV-007 guided state, so pages never hand-place that themselves.
withDefaults(
  defineProps<{
    loading?: boolean;
    error?: boolean;
    empty?: boolean;
    emptyTitle?: string;
    emptyHint?: string;
    emptyAction?: string;
    intent?: ReadIntent | null;
    projectId?: string;
  }>(),
  { loading: false, error: false, empty: false, emptyTitle: '', emptyHint: '', emptyAction: '', intent: null, projectId: '' },
);
const emit = defineEmits<{ (e: 'retry'): void; (e: 'action'): void }>();
</script>

<template>
  <FuiSkeleton v-if="loading" :rows="3" />
  <FuiGuidedEmpty v-else-if="intent && intent.kind !== 'ok'" :intent="intent" :project-id="projectId">
    <template #fallback><slot name="fallback" /></template>
  </FuiGuidedEmpty>
  <FuiEmpty
    v-else-if="error"
    icon="warning-o"
    :title="$t('errors.NETWORK_ERROR')"
    :action-text="$t('common.retry')"
    @action="emit('retry')"
  />
  <FuiEmpty
    v-else-if="empty"
    icon="info-o"
    :title="emptyTitle"
    :hint="emptyHint"
    :action-text="emptyAction"
    @action="emit('action')"
  />
  <slot v-else />
</template>
