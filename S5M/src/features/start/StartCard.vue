<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiButton } from '@/fui';
import { useSetup, usePlatformActions } from '@/api/queries';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { startSteps, blockingSteps } from './startSteps';

/**
 * The first blocking step, on the home page until the configuration is complete (UM-SET-001).
 * Same facts and the same branch table as `/start`, so the two cannot disagree about whether the
 * deployment is ready.
 */
const { t } = useI18n();
const router = useRouter();
const setup = useSetup();
const platform = usePlatformActions();

const create = computed(() => resolveAvailability(indexActions(platform.data.value), ACTION.projectCreate));
const createReason = computed(() => {
  const code = create.value.reasonCode;
  return code ? t(`actionReason.${code}`) : create.value.unknown ? t('actionReason.unknown') : '';
});
const blocking = computed(() =>
  blockingSteps(startSteps(setup.data.value, { available: create.value.available, reason: createReason.value })),
);
const first = computed(() => blocking.value[0]);
</script>

<template>
  <FuiCard
    v-if="first"
    clickable
    :title="t('start.cardTitle', { n: blocking.length })"
    :subtitle="t(`start.step.${first.key}`)"
    @click="router.push('/start')"
  >
    <p class="start-card__note">{{ t(first.noteKey, first.params) }}</p>
    <FuiButton size="small" :label="t('start.cardOpen')" @click.stop="router.push('/start')" />
  </FuiCard>
</template>

<style scoped>
.start-card__note {
  margin: 0 0 var(--fui-sp-2);
  font-size: var(--fui-fs-sm);
  color: var(--fui-text-secondary);
}
</style>
