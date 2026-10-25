<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiActionButton, FuiAsync, FuiButton, FuiCard, FuiDialog, FuiNotice, FuiSegmented, FuiStatusTag } from '@/fui';
import { useBlueprintEntries, useSectionSchema } from '@/api/queries';
import { useSubmitPatch } from '@/api/mutations';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { AppError } from '@/api/errors';
import { entryStatusLabel, nrrPriorityLabel, stageStatusLabel } from '@/lib/labels';
import { pushError, pushSuccess } from '@/lib/toast';
import { useProjectWorkflow } from '@/features/common/useProjectWorkflow';
import type { JsonSchema } from '@/fui/schemaTypes';
import type * as T from '@/api/types';
import RequirementFormSheet from './RequirementFormSheet.vue';
import {
  buildRequirementPatch,
  buildStatusPatch,
  editRationale,
  itemsFromEntries,
  requirementRules,
  type RequirementEdit,
  type RequirementItem,
} from './requirementOps';

const { t, te } = useI18n();
const route = useRoute();
const router = useRouter();

const projectId = computed(() => String(route.params.id ?? ''));
const { workflowId, stages, actions } = useProjectWorkflow(projectId);

// The entries this screen owns live in the blueprint draft, which is where the parse pipeline writes
// them too — one list, hand-authored and extracted rows side by side.
const stage = computed(() => (stages.data.value?.items ?? []).find((s) => s.stageType === 'REQUIREMENTS') ?? null);
const shown = ref<'DRAFT' | 'APPROVED'>('DRAFT');
const sourceInitialised = ref(false);
// A passed requirements stage has no open draft left to edit. Defaulting to DRAFT showed an empty
// screen while the approved entries sat one segment away; open on the version that actually exists.
watch(
  stage,
  (current) => {
    if (!current || sourceInitialised.value) return;
    shown.value = current.status === 'PASSED' ? 'APPROVED' : 'DRAFT';
    sourceInitialised.value = true;
  },
  { immediate: true },
);
const entries = useBlueprintEntries(projectId, () => ({ sections: ['requirements'], source: shown.value }));
const items = computed(() => itemsFromEntries((entries.data.value?.entries ?? []) as unknown as Record<string, unknown>[]));

const schema = useSectionSchema(projectId, 'requirements');
const rules = computed(() => requirementRules(schema.data.value as unknown as JsonSchema | undefined));

/**
 * S1 only answers `patch.submit` while the REQUIREMENTS stage is READY / RUNNING / WAITING_APPROVAL
 * (`domain/actions.py`), and an action it never mentioned is unavailable, not assumed so (UM-BR-02).
 * A closed stage is not a permission problem to guess around: the change-request screen is the way
 * back into an approved blueprint.
 */
const gate = computed(() => resolveAvailability(indexActions(actions.data.value), ACTION.patchSubmit, stage.value?.id ?? null));
const canWrite = computed(() => shown.value === 'DRAFT' && gate.value.available && !!stage.value);
/**
 * Why the screen cannot write, in S1's own terms when it gave any. `unknown` means the action was
 * never mentioned for this stage — which is exactly what a closed stage looks like on the wire.
 */
const blocked = computed(() => {
  if (shown.value === 'APPROVED') return 'approved';
  if (!stage.value || !actions.isSuccess.value || gate.value.available) return '';
  return gate.value.unknown ? 'closed' : 'refused';
});
/** S1's own word for the refusal when this console has one for its code (the `te()`-then-`t()` idiom). */
const refusedText = computed(() => {
  const code = gate.value.reasonCode ?? '';
  return code && te(`errors.${code}`) ? t(`errors.${code}`) : t('reqItems.refused');
});

const patch = useSubmitPatch(() => workflowId.value);
const sheet = ref(false);
const editing = ref<RequirementItem | null>(null);
const deleteTarget = ref<RequirementItem | null>(null);
const approveAllOpen = ref(false);
const statusFilter = ref<'ALL' | 'CANDIDATE' | 'CONFIRMED' | 'REJECTED'>('ALL');
const last = ref<{ kind: 'ok' | 'rejected' | 'stale' | 'invalid'; text: string; issues: Record<string, unknown>[] } | null>(null);

const visibleItems = computed(() =>
  statusFilter.value === 'ALL' ? items.value : items.value.filter((item) => String(item.status ?? 'CANDIDATE') === statusFilter.value),
);
const statusFilters = computed(() => [
  { value: 'ALL', label: t('reqItems.filterAll', { n: items.value.length }) },
  { value: 'CANDIDATE', label: t('reqItems.filterCandidate', { n: items.value.filter((i) => (i.status ?? 'CANDIDATE') === 'CANDIDATE').length }) },
  { value: 'CONFIRMED', label: t('reqItems.filterConfirmed', { n: items.value.filter((i) => i.status === 'CONFIRMED').length }) },
  { value: 'REJECTED', label: t('reqItems.filterRejected', { n: items.value.filter((i) => i.status === 'REJECTED').length }) },
]);
const pendingCount = computed(() => items.value.filter((item) => String(item.status ?? 'CANDIDATE') === 'CANDIDATE').length);

/**
 * One or every entry's verdict, written as the same container replacement the edit path uses.
 * The read is refreshed first for the reason `submitEdit` does it: the parse pipeline can add a
 * row between opening this screen and pressing approve, and a stale array would drop it.
 */
async function submitStatus(keys: readonly string[] | null, status: 'CONFIRMED' | 'REJECTED'): Promise<void> {
  const stageId = stage.value?.id ?? '';
  if (!stageId) return;
  const fresh = await entries.refetch();
  const read = itemsFromEntries((fresh.data?.entries ?? []) as unknown as Record<string, unknown>[]);
  const { operations, changed } = buildStatusPatch(read, keys, status);
  if (!operations.length) {
    pushSuccess(t('reqItems.noStatusChange'));
    return;
  }
  const body = {
    patch: {
      baseVersion: String(fresh.data?.version ?? ''),
      rationale: t('reqItems.rationaleReview', { n: changed }),
      operations,
    },
  } as unknown as T.StagePatchBody;
  patch.mutate(
    { stageId, projectId: projectId.value, body },
    {
      onSuccess: (res) => {
        const view = (res ?? {}) as Record<string, unknown>;
        if (String(view.patchStatus ?? '') === 'REJECTED') {
          last.value = { kind: 'rejected', text: t('reqItems.rejected'), issues: (view.issues ?? []) as Record<string, unknown>[] };
          return;
        }
        last.value = { kind: 'ok', text: t('reqItems.statusSaved', { n: changed }), issues: [] };
        pushSuccess(t('reqItems.statusSaved', { n: changed }));
        approveAllOpen.value = false;
      },
      onError: (err) => {
        const code = err instanceof AppError ? err.errorCode : '';
        if (code === 'SCHEMA_ERROR') last.value = { kind: 'stale', text: t('reqItems.stale'), issues: [] };
        else {
          pushError(err);
          last.value = { kind: 'invalid', text: err instanceof AppError ? err.message : String((err as Error)?.message ?? err), issues: [] };
        }
        void entries.refetch();
      },
    },
  );
}

function openCreate(): void {
  editing.value = null;
  last.value = null;
  sheet.value = true;
}
function openEdit(item: RequirementItem): void {
  editing.value = item;
  last.value = null;
  sheet.value = true;
}

/**
 * Re-read, merge, write.
 *
 * The sheet was opened against a snapshot; an entry the parse pipeline added while it sat open has
 * to be in the array this write sends back, or the set replacement would drop it. `baseVersion`
 * travels so a draft stacked on a different version comes back as a named rejection rather than a
 * patch applied to the wrong baseline.
 */
async function submitEdit(edit: RequirementEdit): Promise<void> {
  const stageId = stage.value?.id ?? '';
  if (!stageId) return;
  const fresh = await entries.refetch();
  const read = itemsFromEntries((fresh.data?.entries ?? []) as unknown as Record<string, unknown>[]);
  const { operations } = buildRequirementPatch(read, edit);
  const body = {
    patch: { baseVersion: String(fresh.data?.version ?? ''), rationale: editRationale(edit, t), operations },
  } as unknown as T.StagePatchBody;

  patch.mutate(
    { stageId, projectId: projectId.value, body },
    {
      onSuccess: (res) => {
        const view = (res ?? {}) as Record<string, unknown>;
        const issues = (view.issues ?? []) as Record<string, unknown>[];
        if (String(view.patchStatus ?? '') === 'REJECTED') {
          last.value = { kind: 'rejected', text: t('reqItems.rejected'), issues };
          return;
        }
        sheet.value = false;
        deleteTarget.value = null;
        last.value = { kind: 'ok', text: okText(edit), issues: [] };
      },
      onError: (err) => {
        sheet.value = false;
        deleteTarget.value = null;
        const code = err instanceof AppError ? err.errorCode : '';
        if (code === 'SCHEMA_ERROR') last.value = { kind: 'stale', text: t('reqItems.stale'), issues: [] };
        else if (code === 'VALIDATION_ERROR') last.value = { kind: 'invalid', text: t('reqItems.invalidRequest'), issues: [] };
        else {
          pushError(err);
          last.value = { kind: 'invalid', text: err instanceof AppError ? err.message : String((err as Error)?.message ?? err), issues: [] };
        }
        void entries.refetch();
      },
    },
  );
}

function okText(edit: RequirementEdit): string {
  const key = edit.kind === 'delete' ? edit.businessKey : String(edit.item.businessKey);
  if (edit.kind === 'delete') return t('reqItems.deleted', { key });
  return t('reqItems.saved', { key });
}

const sourceLabel = (item: RequirementItem): string =>
  item.source?.artifactId ? t('reqItems.fromParse') : item.source?.decisionId ? t('reqItems.fromDecision') : t('reqItems.handWritten');

const confidencePct = (item: RequirementItem): string =>
  item.confidence === undefined || item.confidence === null ? '' : `${Math.round(Number(item.confidence) * 100)}%`;

const toneOf = (kind: 'ok' | 'rejected' | 'stale' | 'invalid') => (kind === 'ok' ? 'info' : 'danger');
</script>

<template>
  <div class="rqs">
    <FuiCard :title="t('reqItems.title')">
      <template #extra>
        <FuiSegmented
          small
          :model-value="shown"
          :options="[
            { value: 'DRAFT', label: t('reqItems.draft') },
            { value: 'APPROVED', label: t('reqItems.approved') },
          ]"
          :label="t('reqItems.draft')"
          @update:model-value="(v: string) => (shown = v as 'DRAFT' | 'APPROVED')"
        />
      </template>
      <p class="rqs__count">{{ t('reqItems.count', { n: items.length }) }}</p>
      <div v-if="items.length" class="rqs__review">
        <FuiSegmented
          small
          :model-value="statusFilter"
          :options="statusFilters"
          :label="t('reqItems.filter')"
          @update:model-value="(v: string) => (statusFilter = v as typeof statusFilter)"
        />
        <FuiActionButton
          v-if="canWrite"
          :workflow-id="workflowId"
          :action="ACTION.patchSubmit"
          :target-id="stage?.id ?? null"
          variant="primary"
          :disabled="pendingCount === 0"
          :label="t('reqItems.approveAllN', { n: pendingCount })"
          @click="approveAllOpen = true"
        />
      </div>
      <FuiActionButton
        v-if="canWrite"
        data-bud="req-create"
        :workflow-id="workflowId"
        :action="ACTION.patchSubmit"
        :target-id="stage?.id ?? null"
        variant="primary"
        block
        round
        :label="t('reqItems.add')"
        @click="openCreate"
      />

      <FuiNotice v-if="blocked === 'approved'" tone="info" :text="t('reqItems.approvedReadonly')" />
      <FuiNotice v-if="shown === 'APPROVED'" tone="info" :text="t('reqItems.approvedProjection')" />
      <FuiNotice v-else-if="blocked === 'closed'" tone="warning">
        <span>{{ t('reqItems.stageClosed', { stage: stageStatusLabel(String(stage?.status ?? '')) }) }}</span>
        <FuiButton size="mini" :label="t('changeRequests.title')" @click="router.push(`/projects/${projectId}/change-requests`)" />
      </FuiNotice>
      <FuiNotice v-else-if="blocked === 'refused'" tone="warning" :text="refusedText" />
      <FuiNotice tone="info" :text="t('reqItems.notInNrr')" />
    </FuiCard>

    <div v-if="last" class="rqs__last">
      <FuiNotice :tone="toneOf(last.kind)" :text="last.text" />
      <FuiCard v-for="(issue, i) in last.issues" :key="i" flat>
        <template #title>{{ issue.ruleId ?? issue.level ?? '' }}</template>
        <p class="rqs__issue">{{ issue.description ?? '' }}</p>
        <p v-if="issue.businessKey" class="rqs__meta">{{ t('reqItems.key') }}: {{ issue.businessKey }}</p>
        <p v-if="issue.suggestedAction" class="rqs__meta">{{ t('reqItems.suggestedAction') }}: {{ issue.suggestedAction }}</p>
      </FuiCard>
      <FuiButton v-if="last.kind === 'stale'" size="small" :label="t('common.refresh')" @click="entries.refetch()" />
    </div>

    <FuiAsync
      :loading="entries.isLoading.value"
      :error="entries.isError.value"
      :empty="!items.length"
      :empty-title="t('reqItems.title')"
      :empty-hint="t('reqItems.emptyHint')"
      :empty-action="canWrite ? t('reqItems.add') : ''"
      @retry="entries.refetch()"
      @action="openCreate"
    >
      <FuiCard v-for="item in visibleItems" :key="String(item.businessKey)">
        <template #title>{{ item.statement }}</template>
        <template #extra>
          <FuiStatusTag
            :tone="item.status === 'REJECTED' ? 'danger' : item.status === 'CONFIRMED' ? 'success' : 'info'"
            :label="entryStatusLabel(String(item.status ?? ''))"
          />
        </template>
        <div class="rqs__meta">
          <span>{{ item.businessKey }}</span>
          <span v-if="item.priority">{{ nrrPriorityLabel(String(item.priority)) }}</span>
          <span v-if="confidencePct(item)">{{ t('reqItems.confidence') }}: {{ confidencePct(item) }}</span>
        </div>
        <div class="rqs__meta">
          <span>{{ sourceLabel(item) }}</span>
          <span v-if="(item.openQuestions ?? []).length">{{ t('reqItems.openQuestions') }}: {{ (item.openQuestions ?? []).length }}</span>
        </div>
        <p v-if="item.rationale" class="rqs__why">{{ item.rationale }}</p>
        <div class="rqs__row">
          <FuiActionButton
            v-if="canWrite && String(item.status ?? 'CANDIDATE') !== 'CONFIRMED'"
            size="small"
            variant="success"
            :workflow-id="workflowId"
            :action="ACTION.patchSubmit"
            :target-id="stage?.id ?? null"
            :label="t('reqItems.approve')"
            @click="submitStatus([String(item.businessKey)], 'CONFIRMED')"
          />
          <FuiActionButton
            v-if="canWrite && item.status !== 'REJECTED'"
            size="small"
            variant="warning"
            :workflow-id="workflowId"
            :action="ACTION.patchSubmit"
            :target-id="stage?.id ?? null"
            :label="t('reqItems.reject')"
            @click="submitStatus([String(item.businessKey)], 'REJECTED')"
          />
          <FuiActionButton
            v-if="canWrite"
            size="small"
            variant="ghost"
            :workflow-id="workflowId"
            :action="ACTION.patchSubmit"
            :target-id="stage?.id ?? null"
            :label="t('common.edit')"
            @click="openEdit(item)"
          />
          <FuiActionButton
            v-if="canWrite"
            size="small"
            variant="danger"
            :workflow-id="workflowId"
            :action="ACTION.patchSubmit"
            :target-id="stage?.id ?? null"
            :label="t('common.delete')"
            @click="deleteTarget = item"
          />
        </div>
      </FuiCard>
      <p v-if="items.length && !visibleItems.length" class="rqs__muted">{{ t('reqItems.noFilterMatch') }}</p>
    </FuiAsync>

    <RequirementFormSheet
      v-model="sheet"
      :item="editing"
      :read="items"
      :rules="rules"
      :stage-id="stage?.id ?? ''"
      :workflow-id="workflowId"
      :saving="patch.isPending.value"
      :failure="patch.error.value"
      @save="submitEdit"
    />

    <FuiDialog
      :show="!!deleteTarget"
      :title="t('common.confirmDelete')"
      show-cancel-button
      :confirm-button-text="t('common.delete')"
      @update:show="(v: boolean) => { if (!v) deleteTarget = null; }"
      @confirm="deleteTarget ? submitEdit({ kind: 'delete', businessKey: String(deleteTarget.businessKey) }) : undefined"
    >
      <p>{{ deleteTarget ? t('reqItems.deleteBody', { key: String(deleteTarget.businessKey) }) : '' }}</p>
    </FuiDialog>

    <FuiDialog
      :show="approveAllOpen"
      :title="t('reqItems.approveAll')"
      show-cancel-button
      :confirm-button-text="t('reqItems.approveAll')"
      :cancel-button-text="t('common.cancel')"
      @update:show="(v: boolean) => { approveAllOpen = v; }"
      @confirm="submitStatus(items.filter((i) => String(i.status ?? 'CANDIDATE') === 'CANDIDATE').map((i) => String(i.businessKey)), 'CONFIRMED')"
    >
      <p>{{ t('reqItems.approveAllBody', { n: pendingCount }) }}</p>
    </FuiDialog>
  </div>
</template>

<style scoped>
.rqs { padding: var(--fui-sp-4); }
.rqs__count { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 0 0 var(--fui-sp-2); }
.rqs__review { display: flex; flex-direction: column; gap: 8px; margin-bottom: var(--fui-sp-3); }
.rqs__muted { font-size: var(--fui-fs-sm); color: var(--fui-text-tertiary); text-align: center; padding: var(--fui-sp-3); }
.rqs__meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 4px; }
.rqs__why { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 6px 0 0; }
.rqs__row { display: flex; gap: 8px; margin-top: var(--fui-sp-3); }
.rqs__last { margin: var(--fui-sp-3) 0; display: flex; flex-direction: column; gap: 8px; }
.rqs__issue { font-size: var(--fui-fs-sm); margin: 0; }
</style>
