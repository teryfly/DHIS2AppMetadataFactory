<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiActionBar, FuiCard, FuiStatusTag, FuiButton, FuiAsync, FuiBottomSheet, FuiActionButton, FuiConclusionCard, FuiNotice, FuiChecklist, FuiCheckboxGroup, FuiCheckbox, FuiField, FuiSegmented } from '@/fui';
import { useGate, useAllowedActions, useGateExplanation } from '@/api/queries';
import { useApproveGate, useRejectGate } from '@/api/mutations';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';
import { ACTION } from '@/api/actions';
import { decisionLabel, decisionLine, decisionText, gateStatusLabel, gateTypeLabel, refTypeLabel, severityLabel, toneForGateStatus, toneForSeverity } from '@/lib/labels';
import { fmtRelTime } from '@/lib/time';
import { usePrefsStore } from '@/stores/prefs';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));

const gateId = computed(() => String(route.params.id ?? ''));
const gateQ = useGate(gateId);
const gate = computed(() => gateQ.data.value);
const explQ = useGateExplanation(gateId);
const expl = computed(() => explQ.data.value);
const openQuestions = computed<Record<string, unknown>[]>(() => ((expl.value?.uncertain as { openQuestions?: Record<string, unknown>[] })?.openQuestions ?? []));
const canChange = computed<Record<string, unknown>[]>(() => (expl.value?.canChange ?? []) as unknown as Record<string, unknown>[]);

/** `why` is a map of category → the decisions in it; the phone reads them as one flat list. */
const whyItems = computed<string[]>(() => {
  const why = (expl.value?.why ?? {}) as Record<string, unknown>;
  const lines = Object.values(why).flatMap((group) => (Array.isArray(group) ? group : [group])).map(decisionLine);
  return lines.length ? lines : [t('gate.noRationale')];
});
const workflowId = computed(() => gate.value?.workflowId ?? '');
useAllowedActions(workflowId);
useWorkflowEvents({ workflowId, projectId: computed(() => ''), enabled: computed(() => !!workflowId.value) });

const warnings = computed(() => gate.value?.warnings ?? []);
const bound = computed(() => gate.value?.boundObjects ?? []);
const confirmations = computed(() => gate.value?.confirmationItems ?? []);
const approvals = computed(() => (gate.value?.approvals ?? []) as unknown as { approver?: string; decision?: string; at?: string }[]);

// Per-warning disposition (UM-BR-06): default unset until the approver acts.
const dispositions = reactive<Record<string, 'ACCEPT' | 'FIX'>>({});
const dispositionOptions = computed(() => [
  { value: 'ACCEPT' as const, label: t('gate.accept') },
  { value: 'FIX' as const, label: t('gate.needFix') },
]);
const confirmChecked = ref<string[]>([]);
const acceptAllStage = ref<'idle' | 'review'>('idle');
const comment = ref('');
const rejectReason = ref('');
// UM-AC-16: no hardcoded Chinese. The presets are the three rejection reasons S1's approval
// checklist recognises, translated like every other label.
const rejectPresets = computed(() => [t('gate.presetMismatch'), t('gate.presetUnverified'), t('gate.presetConflict')]);

const approveSheet = ref(false);
const rejectSheet = ref(false);

const anyFix = computed(() => Object.values(dispositions).some((d) => d === 'FIX'));
// A disposition is recorded per issueId. Requiring one for a warning that has no issueId made the
// gate permanently unapprovable with no stated reason; such a warning is still shown, it just has
// nothing to record.
const allWarningsDisposed = computed(() => warnings.value.every((w) => !w.issueId || dispositions[w.issueId]));
const allRequiredConfirmed = computed(() => confirmations.value.every((c) => !c.required || confirmChecked.value.includes(c.id)));
const canApprove = computed(() => allWarningsDisposed.value && allRequiredConfirmed.value);

function acceptAll(): void {
  acceptAllStage.value = 'review';
}
function confirmAcceptAll(): void {
  for (const w of warnings.value) if (w.issueId) dispositions[w.issueId] = 'ACCEPT';
  acceptAllStage.value = 'idle';
  // §6.1 budget for a gate *with* warnings is 4: 待办卡片 → 全部接受 → 确认全部接受 → 确认批准.
  // Stopping here would cost a fifth click on 批准, so the confirmation panel opens on its own
  // once the reader is entitled to approve.
  if (canApprove.value && gate.value?.status === 'WAITING_APPROVAL') approveSheet.value = true;
}

// §6.1 budget for a gate without warnings is 2: 待办卡片「去审批」→ 底部面板「确认批准」. The todo
// row arrives with ?approve=1; opening the panel here is what removes the intermediate 批准 tap.
// The flag is consumed the moment it opens the sheet: a later SSE-driven refetch that re-runs this
// watch must not re-open a panel the approver already dismissed.
watch(
  () => [route.query.approve, gate.value?.status, warnings.value.length, confirmations.value.length] as const,
  ([flag, status, warningCount, confirmationCount]) => {
    if (flag !== '1' || status !== 'WAITING_APPROVAL') return;
    if (warningCount === 0 && confirmationCount === 0) {
      approveSheet.value = true;
      const { approve: _consumed, ...rest } = route.query;
      void router.replace({ query: rest });
    }
  },
  { immediate: true },
);

const approve = useApproveGate(() => workflowId.value);
const reject = useRejectGate(() => workflowId.value);

function doApprove(): void {
  approve.mutate(
    {
      gateId: gateId.value,
      body: {
        gateType: gate.value?.type ?? '',
        seenBoundHashes: bound.value.map((b) => ({ refId: b.refId, refType: b.refType, contentHash: b.contentHash })),
        warningDispositions: warnings.value.filter((w) => w.issueId).map((w) => ({ issueId: w.issueId, disposition: dispositions[w.issueId ?? ''] })),
        confirmations: confirmChecked.value.map((id) => ({ id, confirmed: true })),
        comment: comment.value || undefined,
      },
    },
    { onSuccess: () => { approveSheet.value = false; router.push('/todo'); } },
  );
}
function doReject(): void {
  if (!rejectReason.value.trim()) return;
  reject.mutate(
    { gateId: gateId.value, body: { gateType: gate.value?.type ?? '', reason: rejectReason.value, warningDispositions: warnings.value.filter((w) => w.issueId).map((w) => ({ issueId: w.issueId, disposition: dispositions[w.issueId ?? ''] })) } },
    { onSuccess: () => { rejectSheet.value = false; router.push('/todo'); } },
  );
}

void severityLabel;
</script>

<template>
  <div class="gt">
    <FuiAsync :loading="gateQ.isLoading.value" :error="gateQ.isError.value" @retry="gateQ.refetch()">
      <template v-if="gate">
        <FuiNotice v-if="gate.status === 'INVALIDATED'" :text="`${t('gate.invalidated')}：${gate.invalidatedReason ?? ''}`" tone="warning" />

        <!-- 页头写清这一节点关掉哪一段活，与桌面 GatePage 的页头同一句话。 -->
        <h2 class="gt__gate">{{ t('gate.title') }} · {{ gateTypeLabel(gate.type) }}</h2>

        <FuiConclusionCard
          :tone="toneForGateStatus(gate.status)"
          :status-text="gateStatusLabel(gate.status)"
          :conclusion="t('gate.willDo')"
          :impact="gate.environmentKey ? `${t('gate.affects')}: ${gate.environmentKey}` : undefined"
          :produce="gate.deploymentRunId ? t('deployments.title') : t('stage.workspace')"
        />

        <!-- approvals timeline -->
        <FuiCard :title="t('gate.approvalsTitle')">
          <div v-if="!approvals.length" class="gt__muted">{{ t('gate.approvalProgress', { n: 0, m: gate.requiredApprovals ?? 1 }) }}</div>
          <div v-for="(a, i) in approvals" :key="i" class="gt__approval">
            <FuiStatusTag :tone="a.decision === 'APPROVED' ? 'success' : 'danger'" :label="a.approver ?? ''" />
            <span class="gt__ameta">{{ decisionLabel(a.decision) }} · {{ fmtRelTime(a.at, lang) }}</span>
          </div>
        </FuiCard>

        <!-- explanation: what was found / why / uncertain / can change (UM-GATE-001) -->
        <FuiCard v-if="expl" :title="t('gate.found')">
          <div class="gt__expl-sec"><b>{{ t('gate.willDo') }}</b> — {{ expl.willDo?.environmentKey ?? gate.environmentKey ?? '' }}
            <span v-if="expl.willDo?.riskSummaryRef"> · {{ expl.willDo.riskSummaryRef }}</span>
          </div>
          <!-- 这一问是一批决策，不是 JSON；手机上一条一行，桌面把同一批画成卡片。 -->
          <div class="gt__expl-sec"><b>{{ t('gate.why') }}</b>
            <div v-for="(w, i) in whyItems" :key="`w-${i}`" class="gt__li">· {{ w }}</div>
          </div>
          <div v-if="openQuestions.length" class="gt__expl-sec"><b>{{ t('gate.uncertain') }}</b>
            <div v-for="(oq, i) in openQuestions" :key="i" class="gt__li">· {{ oq.text ?? oq.question ?? JSON.stringify(oq) }}</div>
          </div>
          <div v-if="canChange.length" class="gt__expl-sec"><b>{{ t('gate.canChange') }}</b>
            <div v-for="(cc, i) in canChange" :key="i" class="gt__li">· {{ cc.label ?? cc.field ?? decisionText(cc) }}</div>
          </div>
        </FuiCard>

        <!-- warnings -->
        <FuiCard v-if="warnings.length" :title="`${t('gate.warnings')} (${warnings.length})`">
          <div class="gt__wa-actions">
            <FuiButton v-if="acceptAllStage === 'idle'" size="small" :label="t('gate.acceptAll')" @click="acceptAll" />
            <template v-else>
              <FuiButton size="small" variant="primary" :label="t('gate.confirmAcceptAll')" @click="confirmAcceptAll" />
              <FuiButton size="small" :label="t('common.cancel')" @click="acceptAllStage = 'idle'" />
            </template>
          </div>
          <div v-for="(w, wi) in warnings" :key="wi" class="gt__warn">
            <div class="gt__warn-head">
              <FuiStatusTag :tone="toneForSeverity(w.level)" :label="severityLabel(w.level)" />
              <span class="gt__warn-msg">{{ w.message }}</span>
            </div>
            <div v-if="w.remediation" class="gt__rem">{{ w.remediation }}</div>
            <FuiSegmented
              v-if="w.issueId"
              v-model="dispositions[w.issueId]"
              class="gt__seg"
              small
              :options="dispositionOptions"
              :label="t('gate.warnings')"
            />
          </div>
        </FuiCard>

        <!-- confirmations -->
        <FuiCard v-if="confirmations.length" :title="t('gate.confirmation')">
          <FuiCheckboxGroup v-model="confirmChecked">
            <FuiCheckbox v-for="c in confirmations" :key="c.id" :name="c.id" shape="square" class="gt__conf">
              {{ c.label }} <span v-if="c.required" class="gt__req">*</span>
            </FuiCheckbox>
          </FuiCheckboxGroup>
        </FuiCard>

        <FuiCard v-if="bound.length" :title="t('gate.boundObjects')">
          <FuiChecklist :items="bound.map((b) => ({ key: b.refId, label: `${refTypeLabel(b.refType)} · ${b.refId}`, state: 'pass', note: b.contentHash.slice(0, 12) }))" />
        </FuiCard>

        <FuiActionBar>
          <template #more>
            <FuiActionButton :workflow-id="workflowId" :action="ACTION.gateReject" :target-id="gateId" size="small" :label="t('gate.reject')" :disabled="gate.status !== 'WAITING_APPROVAL'" @click="rejectSheet = true" />
          </template>
          <template #primary>
            <FuiActionButton :workflow-id="workflowId" :action="ACTION.gateApprove" :target-id="gateId" variant="primary" size="large" block round :label="anyFix ? t('gate.reject') : t('gate.approve')" :disabled="gate.status !== 'WAITING_APPROVAL' || !canApprove" @click="anyFix ? (rejectSheet = true) : (approveSheet = true)" />
          </template>
        </FuiActionBar>
      </template>
    </FuiAsync>

    <FuiBottomSheet v-model="approveSheet" :title="t('gate.approveSummary', { name: gateTypeLabel(gate?.type) })">
      <p class="gt__hint">{{ anyFix ? '' : t('gate.willDo') }}{{ gate?.environmentKey ? ` · ${gate.environmentKey}` : '' }}</p>
      <FuiField v-model="comment" type="textarea" rows="2" :label="t('gate.comment')" />
      <template #footer>
        <!-- The sheet opening is a read; this is the write, so it asks `gate.approve` of
             allowed-actions and shows S1's reason instead of letting an unauthorised approval
             through the moment the checklist happened to be satisfied. -->
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.gateApprove"
          :target-id="gateId"
          variant="primary"
          block
          :loading="approve.isPending.value"
          :disabled="!canApprove"
          :label="t('gate.confirmApprove')"
          @click="doApprove"
        />
      </template>
    </FuiBottomSheet>

    <FuiBottomSheet v-model="rejectSheet" :title="t('gate.rejectSummary', { name: gateTypeLabel(gate?.type) })">
      <div class="gt__presets">
        <FuiButton v-for="p in rejectPresets" :key="p" size="small" :label="p" @click="rejectReason = p" />
      </div>
      <FuiField v-model="rejectReason" type="textarea" rows="3" :label="t('gate.rejectReason')" :placeholder="t('gate.rejectReasonPlaceholder')" />
      <template #footer>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.gateReject"
          :target-id="gateId"
          variant="danger"
          block
          :loading="reject.isPending.value"
          :disabled="!rejectReason.trim()"
          :label="t('gate.confirmReject')"
          @click="doReject"
        />
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.gt { padding: var(--fui-sp-4); padding-bottom: var(--fui-sp-10); }
.gt__muted { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); }
.gt__approval { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
.gt__ameta { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.gt__wa-actions { display: flex; gap: 8px; margin-bottom: 8px; }
.gt__warn { padding: var(--fui-sp-3) 0; border-bottom: 1px solid var(--fui-border); }
.gt__warn-head { display: flex; gap: 8px; align-items: center; }
.gt__warn-msg { font-size: var(--fui-fs-sm); }
.gt__rem { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin: 4px 0; }
.gt__seg { margin-top: 8px; }
.gt__conf { margin: 8px 0; }
.gt__req { color: var(--fui-danger); }
.gt__hint { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 0 0 8px; }
.gt__presets { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.gt__gate { margin: 0 0 8px; font-size: var(--fui-fs-md); font-weight: 600; }
.gt__expl-sec { margin: 8px 0; font-size: var(--fui-fs-sm); }
.gt__pre { white-space: pre-wrap; font-size: var(--fui-fs-xs); background: var(--fui-surface-2); padding: 8px; border-radius: 8px; overflow: auto; margin: 4px 0 0; }
.gt__li { padding: 2px 0; color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); }
</style>
