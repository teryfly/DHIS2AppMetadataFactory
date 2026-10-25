<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiBottomSheet, FuiProgressBar, FuiStatusTag } from '@/fui';
import { useAgentRun } from '@/api/queries';
import { useProgressStore } from '@/stores/progress';
import { runStatusLabel, severityLabel } from '@/lib/labels';
import { fmtRelTime } from '@/lib/time';
import { usePrefsStore } from '@/stores/prefs';
import type * as T from '@/api/types';

const props = withDefaults(
  defineProps<{
    workflowId: string;
    stageId: string;
    stage?: T.StageDetailView;
    requested?: boolean;
    /** Run rows that existed before this click; the newest other row is this attempt. */
    baselineRunIds?: string[];
  }>(),
  { requested: false, baselineRunIds: () => [] },
);
const open = defineModel<boolean>({ required: true });
const { t } = useI18n();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));
const progress = useProgressStore();

const prog = computed(() => progress.get(props.workflowId, 'agent', props.stageId));
const runs = computed(() => props.stage?.stageRuns ?? []);
const baseline = computed(() => new Set(props.baselineRunIds));
const newRuns = computed(() => runs.value.filter((run) => !baseline.value.has(run.id)));
const latestRun = computed(() =>
  newRuns.value.length
    ? newRuns.value[newRuns.value.length - 1]
    : props.requested
      ? undefined
      : runs.value[runs.value.length - 1],
);
const runId = computed(() => prog.value?.runId ?? latestRun.value?.agentRunIds?.[0]);
const agentRun = useAgentRun(
  () => props.workflowId,
  () => runId.value ?? '',
  computed(() => open.value),
);

const result = computed<Record<string, unknown>>(
  () => (latestRun.value?.result as Record<string, unknown> | null | undefined) ?? {},
);
const status = computed(() =>
  String(agentRun.data.value?.status ?? result.value.status ?? props.stage?.status ?? ''),
);
const statusLabel = computed(() =>
  props.requested && ['', 'READY', 'PENDING'].includes(status.value)
    ? t('runLog.requested')
    : runStatusLabel(status.value) || status.value,
);
const live = computed(
  () => status.value === 'RUNNING' || status.value === 'WAITING_INPUT' || (props.requested && ['', 'READY', 'PENDING'].includes(status.value)),
);
const fraction = computed(() =>
  Number(agentRun.data.value?.progress?.completedFraction ?? prog.value?.fraction ?? 0),
);
const step = computed(() => prog.value?.currentStep ?? agentRun.data.value?.progress?.step);
// Only the run this session started has a reliable live timeline: the store is cleared on start,
// while a past run's frames may still contain the parse that preceded it. The final result below
// is always read from the server.
const log = computed(() => (props.requested ? prog.value?.log ?? [] : []));
const summary = computed(() => agentRun.data.value?.summary ?? (result.value.summary as string | null) ?? null);
const issues = computed(() => agentRun.data.value?.issues ?? []);
const denialReasons = computed(() => agentRun.data.value?.denialReasons ?? []);
const rejected = computed(() => agentRun.data.value?.rejectedArtifacts ?? []);
const questions = computed(() => agentRun.data.value?.questions ?? []);

function runTone(value: string): 'success' | 'danger' | 'warning' | 'info' | 'muted' | 'primary' {
  if (value === 'RUNNING') return 'primary';
  if (value === 'COMPLETED' || value === 'PASSED') return 'success';
  if (value === 'WAITING_INPUT' || value === 'WAITING_APPROVAL' || value === 'MANUAL_CHECK') return 'warning';
  if (value === 'FAILED' || value === 'BLOCKED' || value === 'POLICY_DENIED' || value === 'BUDGET_EXCEEDED' || value === 'CANCELLED') return 'danger';
  return 'muted';
}

function refOf(item: Record<string, unknown>): string {
  return typeof item.ref === 'string' ? item.ref : '';
}
function reasonOf(item: Record<string, unknown>): string {
  return typeof item.reason === 'string' ? item.reason : '';
}
</script>

<template>
  <FuiBottomSheet v-model="open" :title="t('runLog.title')">
    <div class="rl">
      <div class="rl__top">
        <FuiStatusTag :tone="runTone(status)" :label="statusLabel" />
        <span v-if="runId" class="rl__run mono">{{ t('runLog.runId') }}: {{ runId }}</span>
        <span v-else-if="requested" class="rl__run">{{ t('runLog.requested') }}</span>
      </div>

      <div class="rl__progress">
        <div class="rl__progress-head">
          <strong>{{ t('runLog.progress') }}</strong>
          <span>{{ Math.round(fraction * 100) }}%</span>
        </div>
        <FuiProgressBar :percentage="Math.round(fraction * 100)" :pivot-text="undefined" />
        <p class="rl__hint">{{ step ? `${t('runLog.latestStep')}: ${step}` : t('runLog.starting') }}</p>
        <p v-if="live" class="rl__hint">{{ t('runLog.liveHint') }}</p>
      </div>

      <h3 class="rl__h">{{ t('runLog.progress') }}</h3>
      <ol v-if="log.length" class="rl__entries">
        <li v-for="entry in log" :key="`${entry.seq}-${entry.at}`">
          <span class="rl__time">{{ fmtRelTime(entry.at, lang) }}</span>
          <span class="rl__msg">{{ entry.message || entry.step || t('runLog.empty') }}</span>
          <span v-if="entry.fraction != null" class="rl__pct">{{ Math.round(entry.fraction * 100) }}%</span>
        </li>
      </ol>
      <p v-else class="rl__hint">{{ requested && live ? t('runLog.waiting') : t('runLog.empty') }}</p>

      <template v-if="summary">
        <h3 class="rl__h">{{ t('runLog.summary') }}</h3>
        <p class="rl__body">{{ summary }}</p>
      </template>

      <template v-if="issues.length">
        <h3 class="rl__h">{{ t('runLog.issues') }}</h3>
        <ul class="rl__list">
          <li v-for="(issue, index) in issues" :key="`${issue.level ?? 'issue'}-${index}`">
            <FuiStatusTag :tone="issue.level === 'BLOCKING' || issue.level === 'ERROR' ? 'danger' : 'warning'" :label="severityLabel(String(issue.level ?? ''))" />
            <span>{{ issue.description }}</span>
          </li>
        </ul>
      </template>

      <template v-if="denialReasons.length">
        <h3 class="rl__h">{{ t('runLog.denied') }}</h3>
        <ul class="rl__list">
          <li v-for="(reason, index) in denialReasons" :key="index">{{ reason }}</li>
        </ul>
      </template>

      <template v-if="rejected.length">
        <h3 class="rl__h">{{ t('runLog.rejected') }}</h3>
        <ul class="rl__list">
          <li v-for="(item, index) in rejected" :key="index">
            <span class="mono">{{ refOf(item as Record<string, unknown>) }}</span>
            <span v-if="reasonOf(item as Record<string, unknown>)"> — {{ reasonOf(item as Record<string, unknown>) }}</span>
          </li>
        </ul>
      </template>

      <p v-if="questions.length" class="rl__hint">{{ t('runLog.questions') }}: {{ questions.length }}</p>
    </div>
  </FuiBottomSheet>
</template>

<style scoped>
.rl { display: flex; flex-direction: column; gap: var(--fui-sp-3); }
.rl__top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rl__run { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); word-break: break-all; }
.rl__progress { display: flex; flex-direction: column; gap: 4px; padding: var(--fui-sp-3); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-md); }
.rl__progress-head { display: flex; justify-content: space-between; font-size: var(--fui-fs-sm); }
.rl__hint { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.rl__h { margin: var(--fui-sp-2) 0 0; font-size: var(--fui-fs-md); }
.rl__entries { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.rl__entries li { display: grid; grid-template-columns: 70px minmax(0, 1fr) auto; gap: 6px; align-items: baseline; padding: 4px 0; border-bottom: 1px solid var(--fui-border); font-size: var(--fui-fs-xs); }
.rl__time, .rl__pct { color: var(--fui-text-tertiary); }
.rl__msg { word-break: break-word; }
.rl__body { margin: 0; font-size: var(--fui-fs-sm); line-height: 1.5; }
.rl__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: var(--fui-fs-xs); }
.rl__list li { display: flex; gap: 6px; align-items: baseline; line-height: 1.5; }
</style>
