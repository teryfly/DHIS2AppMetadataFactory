<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import FuiEmpty from './FuiEmpty.vue';
import FuiButton from './FuiButton.vue';
import { useRequiredStageJump } from '@/lib/stageJump';
import { stageTypeLabel, t } from '@/lib/labels';
import type { ReadIntent } from '@/api/notGenerated';

// UM-REV-007: a derived resource that has not been generated yet gets guidance naming the stage
// that produces it, and a working way to get there — never a blank panel.
const props = withDefaults(
  defineProps<{ intent: ReadIntent; projectId: string }>(),
  { projectId: '' },
);

const router = useRouter();

const requiredStage = computed(() => (props.intent.kind === 'notGenerated' ? props.intent.requiredStage ?? '' : ''));
const jump = useRequiredStageJump(() => props.projectId, requiredStage);

// S1 may name the stage without us being able to resolve it yet; fall back to the raw token so the
// sentence is never "完成后可查看".
const stageName = computed(() => (requiredStage.value ? stageTypeLabel(requiredStage.value) : ''));
const showsStage = computed(() => !!requiredStage.value);

const title = computed(() => {
  switch (props.intent.kind) {
    case 'notGenerated':
      return showsStage.value ? t('notGenerated.awaitingStage', { stage: stageName.value }) : t('notGenerated.awaitingGeneric');
    case 'notExist':
      return t('notGenerated.missing');
    case 'error':
      return props.intent.error?.message || t('errors.NOT_FOUND');
    default:
      return '';
  }
});

const hint = computed(() => {
  if (props.intent.kind !== 'notGenerated' || !showsStage.value) return '';
  return jump.available.value ? '' : t('notGenerated.stageAbsent');
});

const canGo = computed(() => props.intent.kind === 'notGenerated' && showsStage.value && jump.available.value && !jump.pending.value);

function go(): void {
  if (jump.to.value) void router.push(jump.to.value);
}
</script>

<template>
  <FuiEmpty :icon="intent.kind === 'notExist' ? 'info-o' : 'clock-o'" :title="title" :hint="hint">
    <FuiButton v-if="canGo" variant="primary" size="small" :label="t('notGenerated.goStage', { stage: stageName })" @click="go" />
    <slot v-else-if="intent.kind === 'error'" />
    <!-- Whatever the page passed under `fallback` belongs under *any* guided state: S1's own reason,
         for instance, is a sentence about this resource rather than about the stage that would make
         it exist. -->
    <slot name="fallback" />
  </FuiEmpty>
</template>
