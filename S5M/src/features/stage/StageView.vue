<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiConclusionCard, FuiActionBar, FuiActionButton, FuiAsync, FuiProgressRing, FuiBottomSheet, FuiButton, FuiNotice, FuiProgressBar, FuiField, FuiDialog, FuiStatusTag } from '@/fui';
import { useStage, useWorkflow, useQuestions, useAllowedActions } from '@/api/queries';
import { useResolveManualCheck, useRunStage, useSubmitForApproval, useWorkflowFlag, useCancelAgentRun } from '@/api/mutations';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';
import { useProgressStore } from '@/stores/progress';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { stageTypeLabel, stageStatusLabel, toneForStageStatus, runStatusLabel, severityLabel, toneForSeverity } from '@/lib/labels';
import { fmtRelTime } from '@/lib/time';
import { usePrefsStore } from '@/stores/prefs';
import ManualAuthoring from './ManualAuthoring.vue';
import StageChat from './StageChat.vue';
import RunLogSheet from './RunLogSheet.vue';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));
const stageId = computed(() => String(route.params.id ?? ''));

const stageQ = useStage(stageId);
const stage = computed(() => stageQ.data.value);
const workflowId = computed(() => stage.value?.workflowId ?? '');
const actions = useAllowedActions(workflowId);
const workflowQ = useWorkflow(workflowId);
const workflowProjectId = computed(() => workflowQ.data.value?.projectId ?? '');
useWorkflowEvents({ workflowId, projectId: workflowProjectId, enabled: computed(() => !!workflowId.value) });

const questionsQ = useQuestions(workflowId);
const stageQuestions = computed(() => (questionsQ.data.value?.items ?? []).filter((q) => q.stageId === stageId.value && q.status === 'OPEN'));

// AI-CHAT-001 scopes a dialogue to a design stage; S1 and S3 refuse the read for every other stage
// type, so on a DISCOVERY or compile stage the panel would only announce a missing object.
const chatable = computed(() => ['REQUIREMENTS', 'DOMAIN', 'MAPPING', 'PAGE_DESIGN'].includes(String(stage.value?.stageType ?? '')));

const progress = useProgressStore();
const runRequested = ref(false);
const logOpen = ref(String(route.query.log ?? '') === '1');
const baselineRunIds = ref<string[]>([]);
let runPoll: ReturnType<typeof setInterval> | null = null;

const prog = computed(() => (workflowId.value ? progress.get(workflowId.value, 'agent', stageId.value) : undefined));
const running = computed(() => stage.value?.status === 'RUNNING' || runRequested.value);
const liveRun = computed(() => {
  const status = String(stage.value?.status ?? '');
  return status === 'RUNNING' || (runRequested.value && ['', 'READY', 'PENDING'].includes(status));
});

function stopRunPolling(): void {
  if (runPoll) {
    clearInterval(runPoll);
    runPoll = null;
  }
}
function startRunPolling(): void {
  stopRunPolling();
  const startedAt = Date.now();
  runPoll = setInterval(async () => {
    await stageQ.refetch();
    const status = String(stageQ.data.value?.status ?? '');
    // The claim is synchronous, but a stale response can still arrive first. Keep asking for a
    // few seconds, then stop as soon as the stage reaches a terminal state.
    if (Date.now() - startedAt > 5_000 && status && !['READY', 'PENDING', 'RUNNING', 'MANUAL_CHECK'].includes(status)) {
      stopRunPolling();
    }
  }, 2_000);
}
onScopeDispose(stopRunPolling);
watch(stageId, () => {
  stopRunPolling();
  runRequested.value = false;
  logOpen.value = String(route.query.log ?? '') === '1';
  baselineRunIds.value = [];
});

function runTone(value: string): 'success' | 'danger' | 'warning' | 'info' | 'muted' | 'primary' {
  if (value === 'RUNNING') return 'primary';
  if (value === 'COMPLETED' || value === 'PASSED') return 'success';
  if (value === 'WAITING_INPUT' || value === 'WAITING_APPROVAL' || value === 'MANUAL_CHECK') return 'warning';
  if (value === 'FAILED' || value === 'BLOCKED' || value === 'POLICY_DENIED' || value === 'BUDGET_EXCEEDED' || value === 'CANCELLED') return 'danger';
  return 'muted';
}

const idx = computed(() => indexActions(actions.data.value));
const can = (action: string) => resolveAvailability(idx.value, action, stageId.value).available;

type Primary = { action: string; label: string } | null;
const primaryAction = computed<Primary>(() => {
  if (stage.value?.status === 'WAITING_APPROVAL' && can(ACTION.stageSubmitForApproval)) return { action: ACTION.stageSubmitForApproval, label: t('stage.submitApproval') };
  if (can(ACTION.stageRun)) return { action: ACTION.stageRun, label: t('stage.run') };
  if (can(ACTION.stageSubmitForApproval)) return { action: ACTION.stageSubmitForApproval, label: t('stage.submitApproval') };
  if (can(ACTION.stageRerun)) return { action: ACTION.stageRerun, label: t('stage.rerun') };
  return null;
});

const runStage = useRunStage(() => workflowId.value);
const submitApproval = useSubmitForApproval(() => workflowId.value);
function startRun(rerun: boolean): void {
  baselineRunIds.value = (stage.value?.stageRuns ?? []).map((run) => run.id);
  if (workflowId.value) progress.clear(`${workflowId.value}:agent:${stageId.value}`);
  // Show the live card and the log before the server answers: the action button disappears the
  // moment the claim lands, and a screen with neither the button nor a result reads as "nothing".
  runRequested.value = true;
  logOpen.value = true;
  startRunPolling();
  runStage.mutate(
    { stageId: stageId.value, rerun },
    {
      onError: () => {
        stopRunPolling();
        runRequested.value = false;
        logOpen.value = false;
      },
    },
  );
}

function onPrimary(): void {
  const p = primaryAction.value;
  if (!p) return;
  if (p.action === ACTION.stageRun) startRun(false);
  else if (p.action === ACTION.stageRerun) startRun(true);
  else submitApproval.mutate({ stageId: stageId.value });
}

const checkSheet = ref(false);
const checkNote = ref('');
const resolve = useResolveManualCheck(() => workflowId.value);
function markResult(outcome: 'PASSED' | 'FAILED'): void {
  resolve.mutate({ stageId: stageId.value, body: { outcome, note: checkNote.value } }, { onSuccess: () => (checkSheet.value = false) });
}

const manualSheet = ref(false);

// Workflow-level control (UM-WB): pause / resume is one switch whose name follows the state the
// operator is acting on, and cancel is the one that cannot be undone -- it asks first. Which of
// them is clickable is still S1's answer, never a reading of `paused` alone (UM-BR-01).
const flag = useWorkflowFlag();

// Cancel one Agent run, not the workflow. S1's endpoint exists; the run id arrives on the SSE
// progress frames, and availability is S1's answer to `agent.cancel` — the screen does not guess.
const cancelAgent = useCancelAgentRun();
const agentCancelPrompt = ref(false);
function cancelAgentRun(): void {
  const runId = prog.value?.runId;
  if (!runId) return;
  cancelAgent.mutate(
    { workflowId: workflowId.value, projectId: workflowProjectId.value, runId, stageId: stageId.value },
    { onSuccess: () => progress.clear(`${workflowId.value}:agent:${stageId.value}`) },
  );
}
const paused = computed(() => workflowQ.data.value?.paused === true);
const cancelled = computed(() => workflowQ.data.value?.cancelled === true);
const cancelPrompt = ref(false);
function setFlag(toPause: boolean): void {
  flag.mutate({ workflowId: workflowId.value, projectId: workflowProjectId.value, flag: toPause ? 'pause' : 'resume' });
}

const busy = computed(() => runStage.isPending.value || submitApproval.isPending.value || resolve.isPending.value || flag.isPending.value);
</script>

<template>
  <div class="stg">
    <FuiAsync :loading="stageQ.isLoading.value" :error="stageQ.isError.value" @retry="stageQ.refetch()">
      <template v-if="stage">
        <FuiConclusionCard
          :tone="toneForStageStatus(stage.status)"
          :conclusion="stageTypeLabel(stage.stageType)"
          :impact="stage.feedback?.reason && ['FAILED', 'BLOCKED', 'INVALIDATED'].includes(String(stage.status)) ? t('stage.rejectedReason') + ': ' + stage.feedback.reason : undefined"
        >
          <template #status>
            <button type="button" class="stg__status-btn" :aria-label="t('runLog.open')" @click="logOpen = true">
              <FuiStatusTag :tone="toneForStageStatus(stage.status)" :label="stageStatusLabel(stage.status)" />
              <span class="stg__status-link">{{ t('runLog.open') }}</span>
            </button>
          </template>
          <template #summary>
            <div class="stg__stats">
              <div class="stg__ring"><FuiProgressRing :percent="Math.round((stage.inputCompleteness?.ratio ?? 0) * 100)" /> <span>{{ t('stage.inputCompleteness') }}</span></div>
              <div class="stg__counts">
                <span>{{ t('stage.openQuestions') }}: <b>{{ stage.openQuestionCount ?? 0 }}</b></span>
                <span>{{ t('stage.blockingIssues') }}: <b>{{ stage.blockingIssueCount ?? 0 }}</b></span>
                <span>{{ t('stage.lastRun') }}: {{ stage.lastRunAt ? fmtRelTime(stage.lastRunAt, lang) : '—' }}</span>
              </div>
            </div>
          </template>
          <template v-if="stage.feedback && (stage.status === 'FAILED' || stage.status === 'BLOCKED')" #details>
            <FuiNotice :text="stage.feedback.reason ?? ''" tone="danger" />
            <ul v-if="stage.feedback.denialReasons?.length" class="stg__denials">
              <li v-for="(d, i) in stage.feedback.denialReasons" :key="i">{{ d }}</li>
            </ul>
          </template>
        </FuiConclusionCard>

        <FuiCard v-if="running || runRequested" :title="liveRun ? t('stage.progress') : t('runLog.resultTitle')">
          <div class="stg__run-head">
            <FuiStatusTag
              :tone="runTone(String(stage.status))"
              :label="liveRun && stage.status !== 'RUNNING' ? t('runLog.requested') : runStatusLabel(stage.status)"
            />
            <span v-if="runRequested && !liveRun" class="stg__hint">{{ t('runLog.requested') }}</span>
          </div>
          <FuiProgressBar :percentage="Math.round((prog?.fraction ?? 0) * 100)" :pivot-text="undefined" />
          <p class="stg__step">{{ prog?.message ?? prog?.currentStep ?? t('runLog.starting') }}</p>
          <p class="stg__hint">{{ t('stage.doneLeaveHint') }}</p>
          <div class="stg__run-actions">
            <FuiButton size="small" :label="t('runLog.open')" @click="logOpen = true" />
            <FuiActionButton
              v-if="prog?.runId"
              :workflow-id="workflowId"
              :action="ACTION.agentCancel"
              :target-id="prog.runId"
              size="small"
              variant="danger"
              :label="t('stage.cancelAgentRun')"
              @click="agentCancelPrompt = true"
            />
          </div>
        </FuiCard>

        <FuiCard v-if="stage.status === 'MANUAL_CHECK'" :title="t('stageStatus.MANUAL_CHECK')">
          <FuiButton block :label="t('stage.markResult')" @click="checkSheet = true" />
        </FuiCard>

        <FuiCard v-if="stage.stageType === 'REQUIREMENTS'" :title="t('reqItems.reviewTitle')">
          <p class="stg__hint">{{ t('reqItems.reviewHint') }}</p>
          <FuiButton
            block
            :label="t('reqItems.reviewTitle')"
            @click="router.push(`/projects/${workflowProjectId}/requirements`)"
          />
        </FuiCard>

        <FuiCard v-if="stageQuestions.length" :title="`${t('questions.title')} · ${stageQuestions.length}`">
          <p class="stg__hint">{{ t('questions.answerHint') }}</p>
          <div class="stg__questions">
            <div v-for="q in stageQuestions" :key="q.id" class="stg__question">
              <div class="stg__question-head">
                <FuiStatusTag :tone="toneForSeverity(String(q.severity))" :label="severityLabel(String(q.severity))" />
                <FuiStatusTag v-if="q.blocking" tone="danger" :label="t('questions.blockingTag')" />
              </div>
              <p class="stg__question-text">{{ q.text }}</p>
              <FuiButton
                size="small"
                variant="primary"
                :label="t('questions.answer')"
                @click="router.push('/questions/' + q.id)"
              />
            </div>
          </div>
        </FuiCard>

        <StageChat v-if="chatable" :stage-id="stageId" :workflow-id="workflowId" />

        <FuiCard :title="t('stage.workspace')">
          <div class="stg__entries">
            <FuiButton size="small" :label="t('inputs.title')" @click="router.push('/projects/' + workflowProjectId + '/inputs')" />
            <FuiButton size="small" :label="t('review.artifacts')" @click="router.push('/projects/' + workflowProjectId + '/artifacts')" />
            <FuiButton size="small" :label="t('review.metadata')" @click="router.push('/projects/' + workflowProjectId + '/metadata')" />
            <FuiButton size="small" :label="t('manual.title')" @click="manualSheet = true" />
          </div>
          <!-- The whole-workflow controls live here rather than in the sticky action bar: that bar
               carries one primary action and a refusal reason under a button would push it off
               the screen. Pause/resume is one switch; cancel asks first. -->
          <div v-if="!cancelled" class="stg__entries stg__wf">
            <FuiActionButton
              :workflow-id="workflowId"
              :action="paused ? ACTION.workflowResume : ACTION.workflowPause"
              size="small"
              :label="paused ? t('stage.resumeWorkflow') : t('stage.pauseWorkflow')"
              :loading="flag.isPending.value"
              @click="setFlag(!paused)"
            />
            <FuiActionButton
              :workflow-id="workflowId"
              :action="ACTION.workflowCancel"
              size="small"
              variant="danger"
              :label="t('stage.cancelWorkflow')"
              @click="cancelPrompt = true"
            />
          </div>
          <p v-else class="stg__hint">{{ t('actionReason.WORKFLOW_CANCELLED') }}</p>
        </FuiCard>

        <FuiActionBar>
          <template #more>
            <FuiActionButton :workflow-id="workflowId" :action="ACTION.stageRerun" :target-id="stageId" size="small" :label="t('stage.rerun')" @click="startRun(true)" />
          </template>
          <template #primary>
            <FuiActionButton v-if="primaryAction" :workflow-id="workflowId" :action="primaryAction.action" :target-id="stageId" variant="primary" size="large" block round :label="primaryAction.label" :loading="busy" @click="onPrimary" />
          </template>
        </FuiActionBar>
      </template>
    </FuiAsync>

    <RunLogSheet
      v-model="logOpen"
      :workflow-id="workflowId"
      :stage-id="stageId"
      :stage="stage"
      :requested="runRequested"
      :baseline-run-ids="baselineRunIds"
    />

    <FuiBottomSheet v-model="checkSheet" :title="t('stage.markResult')">
      <FuiField v-model="checkNote" type="textarea" rows="2" :label="t('stage.manualNote')" />
      <template #footer>
        <div class="stg__check-actions">
          <!-- The verdict writes state, so it asks `stage.resolve_manual_check` of allowed-actions
               and shows S1's reason when it says no — same rule as the run/rerun buttons above. -->
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.stageResolveManualCheck" :target-id="stageId" variant="success" block :label="t('stage.manualPassed')" @click="markResult('PASSED')" />
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.stageResolveManualCheck" :target-id="stageId" variant="danger" block :label="t('stage.manualFailed')" @click="markResult('FAILED')" />
        </div>
      </template>
    </FuiBottomSheet>

    <ManualAuthoring v-model="manualSheet" :stage-id="stageId" :project-id="workflowProjectId" :stage-type="stage?.stageType ?? ''" :workflow-id="workflowId" />

    <FuiDialog
      v-model:show="cancelPrompt"
      :title="t('stage.cancelWorkflow')"
      show-cancel-button
      :confirm-button-text="t('common.confirm')"
      :cancel-button-text="t('common.cancel')"
      @confirm="flag.mutate({ workflowId, projectId: workflowProjectId, flag: 'cancel' })"
    >
      <p class="stg__hint">{{ t('stage.cancelWorkflowBody') }}</p>
    </FuiDialog>

    <FuiDialog
      v-model:show="agentCancelPrompt"
      :title="t('stage.cancelAgentRun')"
      show-cancel-button
      :confirm-button-text="t('common.confirm')"
      :cancel-button-text="t('common.cancel')"
      @confirm="cancelAgentRun"
    >
      <p class="stg__hint">{{ t('stage.cancelAgentRunBody') }}</p>
    </FuiDialog>
  </div>
</template>

<style scoped>
.stg { padding: var(--fui-sp-4); padding-bottom: var(--fui-sp-8); }
.stg__stats { display: flex; gap: var(--fui-sp-4); align-items: center; }
.stg__ring { display: flex; flex-direction: column; align-items: center; gap: 4px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.stg__counts { display: flex; flex-direction: column; gap: 4px; font-size: var(--fui-fs-sm); }
.stg__denials { margin: 8px 0; padding-left: 20px; font-size: var(--fui-fs-sm); }
.stg__step { margin: 8px 0 0; font-size: var(--fui-fs-sm); }
.stg__hint { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin: 6px 0 0; }
.stg__status-btn { display: inline-flex; align-items: center; gap: 8px; padding: 0; border: 0; background: none; cursor: pointer; }
.stg__status-link { color: var(--fui-primary); font-size: var(--fui-fs-xs); }
.stg__run-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.stg__run-actions { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; margin-top: var(--fui-sp-3); }
.stg__question { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: var(--fui-sp-3); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-md); }
.stg__question-head { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.stg__question-text { margin: 0; font-size: var(--fui-fs-sm); line-height: 1.6; white-space: normal; overflow-wrap: anywhere; }
.stg__questions { display: flex; flex-direction: column; gap: var(--fui-sp-3); margin-top: var(--fui-sp-2); }
.stg__entries { display: flex; flex-wrap: wrap; gap: 8px; }
.stg__wf { margin-top: var(--fui-sp-3); padding-top: var(--fui-sp-3); border-top: 1px solid var(--fui-border); }
.stg__check-actions { display: flex; gap: 8px; }
</style>
