<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiChecklist, FuiActionButton, FuiSlideConfirm, FuiNotice, FuiBottomSheet, FuiButton, FuiField } from '@/fui';
import { useRunSummary, useRun, useExecution, useRunBindings, useBindingCandidates, useAllowedActions } from '@/api/queries';
import { useCommitRun, useDriftCheck, useResolveBinding, useReconcile, useCancelRun } from '@/api/mutations';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';
import { AppError } from '@/api/errors';
import { envClassLabel, stageStatusLabel, toneForRunStatus } from '@/lib/labels';
import type { CheckItem } from '@/fui/types';
import type { BindingView, CandidateView } from '@/api/types';

const { t } = useI18n();
const route = useRoute();
const runId = computed(() => String(route.params.id ?? ''));
const run = useRun(runId);
// This screen is reached from a notification or a link, not from inside the project layout, so the
// workflow stream is not mounted here by default — see the useWorkflowEvents call below.
const live = computed(() => !['COMMITTED', 'VERIFIED', 'FAILED', 'CANCELLED'].includes(String((run.data.value as unknown as { status?: string } | undefined)?.status ?? '')));
const summary = useRunSummary(runId, live);
const commit = useCommitRun();
const drift = useDriftCheck();
const cancel = useCancelRun();
const commitPending = ref(false);

const s = computed(() => summary.data.value);
const workflowId = computed(() => String((s.value as unknown as { workflowId?: string } | undefined)?.workflowId ?? ''));
// Same reason as on the gate screen: without the stream this console freezes while the run moves.
useWorkflowEvents({ workflowId, projectId: computed(() => ''), enabled: computed(() => !!workflowId.value) });
const actions = useAllowedActions(workflowId);
// COMMIT used to be gated on a locally hardcoded status list that included FAILED, i.e. the UI
// deciding for itself that a failed run may be written to the environment. S1 answers that.
const commitAvailability = computed(() => resolveAvailability(indexActions(actions.data.value), ACTION.executionCommit, runId.value));
const canCommit = computed(() => commitAvailability.value.available);
const commitReason = computed(() => {
  const code = commitAvailability.value.reasonCode;
  if (code) return t(`actionReason.${code}`);
  return commitAvailability.value.available ? '' : t('actionReason.unknown');
});
const isProd = computed(() => (s.value?.environmentClass ?? '').toString().toUpperCase() === 'PRODUCTION');
const executionId = computed(() => String((run.data.value as unknown as { executionId?: string } | undefined)?.executionId ?? (s.value?.execution as { executionId?: string } | undefined)?.executionId ?? ''));

function state(v: unknown): CheckItem['state'] {
  if (v === true) return 'pass';
  if (v === false) return 'fail';
  if (v == null) return 'pending';
  const str = typeof v === 'object' ? JSON.stringify(v).toLowerCase() : String(v).toLowerCase();
  if (/(passed|ok|true|green|verified|satisfied|success)/.test(str)) return 'pass';
  if (/(fail|error|red|denied|locked|missing|drift)/.test(str)) return 'fail';
  return 'pending';
}

const checks = computed<CheckItem[]>(() => {
  const d = s.value;
  if (!d) return [];
  return [
    { key: 'conn', label: t('deployments.connection'), state: state(d.connection) },
    { key: 'cap', label: t('deployments.capability'), state: state(d.capabilityFreshness) },
    { key: 'drift', label: t('deployments.drift'), state: state(d.drift) },
    { key: 'snap', label: t('deployments.snapshot'), state: state(d.snapshot) },
    { key: 'static', label: t('deployments.staticValidate'), state: state(d.validation) },
    { key: 'appr', label: t('deployments.approvalProgress'), state: state(d.approval) },
    { key: 'window', label: t('deployments.commitWindow'), state: state(d.commitWindow) },
    { key: 'verify', label: t('deployments.postVerify'), state: state((d as Record<string, unknown>).verification) },
  ];
});

const stats = computed(() => (s.value?.changeStats ?? {}) as { create?: number; update?: number; delete?: number; externalReferences?: number });
const payloadHash = computed(() => {
  const p = (s.value as Record<string, unknown>)?.payload as { hash?: string; contentHash?: string } | undefined;
  return String(p?.hash ?? p?.contentHash ?? '').slice(-8);
});
function onCommit(): void {
  // "result pending confirmation" only stands while nobody has answered. Any settled outcome —
  // a replayed result, a refusal, a fresh success — resolves the uncertainty, so the banner goes.
  commit.mutate(runId.value, {
    onSuccess: () => { commitPending.value = false; },
    onError: (err) => {
      if (err instanceof AppError && err.networkError) commitPending.value = true;
      else commitPending.value = false;
    },
  });
}

// ---- environment bindings resolution (UM-DEP-004)
const bindings = useRunBindings(runId);
const bindingList = computed<BindingView[]>(() => (bindings.data.value?.items ?? []) as BindingView[]);
const unresolved = computed(() => bindingList.value.filter((b) => !b.uid || b.pendingReparse || (b.resolution && String(b.resolution).toLowerCase().includes('unresolved'))));
const pickSheet = ref(false);
const pickKey = ref('');
const candidates = useBindingCandidates(runId, pickKey);
const candList = computed<CandidateView[]>(() => (candidates.data.value?.items ?? []) as CandidateView[]);
const resolve = useResolveBinding();
const pickNote = ref('');
function openPick(key: string): void {
  pickKey.value = key;
  pickNote.value = '';
  pickSheet.value = true;
  candidates.refetch();
}
function choose(c: CandidateView): void {
  if (!c.uid) return;
  resolve.mutate({ runId: runId.value, businessKey: pickKey.value, body: { uid: String(c.uid), note: pickNote.value } }, { onSuccess: () => (pickSheet.value = false) });
}

// ---- execution report / manual-check reconcile
const execution = useExecution(executionId);
const reconcile = useReconcile();
const recResult = ref('');
function doReconcile(): void {
  reconcile.mutate(executionId.value, { onSuccess: (res) => (recResult.value = JSON.stringify((res as Record<string, unknown>).evidence ?? res)) });
}
</script>

<template>
  <div class="rc">
    <FuiAsync :loading="summary.isLoading.value" :error="summary.isError.value" @retry="summary.refetch()">
      <template v-if="s">
        <FuiCard>
          <template #title>{{ s.environmentKey }}</template>
          <template #extra><FuiStatusTag :tone="toneForRunStatus(s.status)" :label="stageStatusLabel(s.status)" /></template>
          <div class="rc__meta">
            <span>{{ envClassLabel(s.environmentClass) }}</span>
            <span>{{ t('review.versions') }}: {{ s.blueprintVersion }}</span>
            <FuiStatusTag v-if="s.outdated" tone="muted" :label="t('deployments.outdated')" />
          </div>
        </FuiCard>

        <FuiCard :title="t('deployments.checklist')">
          <FuiChecklist :items="checks">
            <template #action="{ item }">
              <FuiActionButton
                v-if="item.key === 'drift'"
                size="mini"
                :action="ACTION.driftCheck"
                :workflow-id="workflowId"
                :target-id="runId"
                :label="t('deployments.recheckDrift')"
                :loading="drift.isPending.value"
                @click="drift.mutate(runId)"
              />
            </template>
          </FuiChecklist>
        </FuiCard>

        <FuiCard :title="t('deployments.changeStats')">
          <div class="rc__stats">
            <span class="rc__st rc__st--c">{{ t('review.created') }} {{ stats.create ?? 0 }}</span>
            <span class="rc__st rc__st--u">{{ t('review.updated') }} {{ stats.update ?? 0 }}</span>
            <span class="rc__st rc__st--d">{{ t('review.deleted') }} {{ stats.delete ?? 0 }}</span>
            <span class="rc__st">{{ t('review.externalRef') }} {{ stats.externalReferences ?? 0 }}</span>
          </div>
        </FuiCard>

        <!-- unresolved environment bindings -->
        <FuiCard v-if="unresolved.length" :title="t('deployments.bindingUnresolved')">
          <div v-for="b in unresolved" :key="b.businessKey" class="rc__bind">
            <span class="rc__bindkey">{{ b.businessKey }}</span>
            <FuiButton size="small" :label="t('deployments.chooseCandidate')" @click="openPick(b.businessKey)" />
          </div>
        </FuiCard>

        <!-- execution report / post-verify -->
        <FuiCard v-if="executionId && execution.data.value" :title="t('deployments.report')">
          <div class="rc__exhead">
            <FuiStatusTag :tone="toneForRunStatus(execution.data.value.status)" :label="stageStatusLabel(String(execution.data.value.status))" />
            <span class="rc__exid">{{ executionId }}</span>
          </div>
          <p v-if="execution.data.value.issues?.length" class="rc__issues">{{ t('review.issues') }}: {{ execution.data.value.issues.length }}</p>
          <FuiButton size="small" :label="t('deployments.importReport')" :disabled="!execution.data.value.reportArtifactId" @click="execution.data.value.reportArtifactId && $router.push('/artifacts/' + execution.data.value.reportArtifactId)" />
          <FuiButton size="small" :label="t('stage.viewReconcile')" :loading="reconcile.isPending.value" @click="doReconcile" />
          <pre v-if="recResult" class="rc__rec">{{ recResult }}</pre>
        </FuiCard>

        <FuiNotice v-if="commitPending" :text="t('deployments.commitPendingConfirm')" tone="warning" />

        <FuiCard :title="t('deployments.commit')">
          <p class="rc__hint">{{ t('deployments.payloadHash', { hash: payloadHash }) }} · {{ envClassLabel(s.environmentClass) }}</p>
          <!-- The ghost COMMIT button had no handler at all: it emitted `click` to nobody and only
               the slide below did the work. One control, and it asks allowed-actions. -->
          <!-- The run could be watched going wrong with no way to stop it. S1 refuses a cancel once
               the COMMIT has started, which is why this asks allowed-actions rather than reading the
               badge. -->
          <FuiActionButton
            :workflow-id="s.workflowId ?? ''"
            :action="ACTION.deploymentCancel"
            :target-id="runId"
            :label="t('deployments.cancelRun')"
            variant="danger"
            size="small"
            :loading="cancel.isPending.value"
            @click="cancel.mutate({ runId, workflowId: s.workflowId ?? '' })"
          />
          <FuiSlideConfirm
            class="rc__slide"
            :tone="isProd ? 'danger' : 'primary'"
            :text="t('deployments.commitSlide', { env: s.environmentKey + (isProd ? ' (PROD)' : '') })"
            :disabled="!canCommit"
            @confirm="onCommit"
          />
          <p v-if="!canCommit && commitReason" class="rc__why">{{ commitReason }}</p>
        </FuiCard>
      </template>
    </FuiAsync>

    <FuiBottomSheet v-model="pickSheet" :title="`${t('deployments.candidateSearch')}: ${pickKey}`">
      <FuiField v-model="pickNote" :label="t('deployments.bindingNote')" />
      <div v-if="candidates.isLoading.value" class="rc__loading">{{ t('common.loading') }}</div>
      <button v-for="c in candList" :key="String(c.uid)" class="rc__cand" @click="choose(c)">
        <div class="rc__cand-name">{{ c.name || c.code || c.uid }}</div>
        <div class="rc__cand-meta">{{ c.dhis2Type }} <FuiStatusTag v-if="c.matching" tone="success" :label="t('deployments.passedCheck')" /></div>
      </button>
      <p v-if="!candList.length && !candidates.isLoading.value" class="rc__muted">{{ t('nav.noResults') }}</p>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.rc { padding: var(--fui-sp-4); }
.rc__meta { display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.rc__stats { display: flex; flex-wrap: wrap; gap: 12px; font-size: var(--fui-fs-sm); }
.rc__st--c { color: var(--fui-success); }
.rc__st--u { color: var(--fui-info); }
.rc__st--d { color: var(--fui-danger); }
.rc__hint { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 0 0 8px; }
.rc__slide { margin-top: var(--fui-sp-2); }
.rc__bind { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.rc__bindkey { font-size: var(--fui-fs-sm); }
.rc__exhead { display: flex; gap: 8px; align-items: center; }
.rc__exid { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.rc__issues { color: var(--fui-danger); font-size: var(--fui-fs-sm); }
.rc__cand { width: 100%; text-align: left; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); padding: 10px; margin: 6px 0; min-height: 44px; }
.rc__cand-name { font-size: var(--fui-fs-sm); font-weight: 600; }
.rc__cand-meta { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); display: flex; gap: 8px; align-items: center; }
.rc__loading, .rc__muted { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); text-align: center; padding: 12px; }
.rc__rec { white-space: pre-wrap; font-size: var(--fui-fs-xs); background: var(--fui-surface-2); padding: 8px; border-radius: 8px; margin-top: 8px; }
</style>
