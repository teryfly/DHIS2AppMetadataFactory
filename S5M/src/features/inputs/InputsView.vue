<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { useQueryClient } from '@tanstack/vue-query';
import { FuiCard, FuiButton, FuiUpload, FuiStatusTag, FuiAsync, FuiBottomSheet, FuiActionButton, FuiRadioGroup, FuiRadio, FuiNotice } from '@/fui';
import { useInputs, useUploadLimits, useWorkflowOfProject, useNrr, useQuestions, useBlueprintEntries, useAllowedActions } from '@/api/queries';
import { useParseInput } from '@/api/mutations';
import { uploadInput, type DataClassification } from '@/api/upload';
import { qk } from '@/api/keys';
import { AppError } from '@/api/errors';
import { useUiStore } from '@/stores/ui';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { pushSuccess, pushWarn, pushError, pushInfo } from '@/lib/toast';
import { classificationLabel, parseStatusLabel } from '@/lib/labels';
import { itemsFromEntries } from '@/features/requirements/requirementOps';
import QuestionnaireForm from '@/features/questions/QuestionnaireForm.vue';
import type * as T from '@/api/types';

const { t, te } = useI18n();
const uploader = ref<{ pickFile: () => void } | null>(null);
const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const qc = useQueryClient();

const projectId = computed(() => String(route.params.id ?? ''));
const inputs = useInputs(projectId);
const limits = useUploadLimits();
const parse = useParseInput();

// Normalized requirements + draft requirement entries (UM-REQ-003)
const workflow = useWorkflowOfProject(projectId);
const wfId = computed(() => workflow.data.value?.id ?? '');
const nrr = useNrr(wfId);
const stageQuestions = useQuestions(wfId);
const requirementsStage = computed(() =>
  (workflow.data.value?.stages ?? []).find((stage) => stage.stageType === 'REQUIREMENTS'),
);
/**
 * The questions this parse raised, which are the review actions a person can actually take.
 * The NRR entries themselves are read-only candidates; answering these questions and approving
 * the requirements stage is what turns them into conclusions.
 */
const reviewQuestions = computed(() =>
  (stageQuestions.data.value?.items ?? []).filter(
    (question) => question.stageId === requirementsStage.value?.id && question.status === 'OPEN',
  ),
);
const draftEntriesQ = useBlueprintEntries(projectId, { sections: ['requirements'], source: 'DRAFT' });
const nrrItems = computed(() => nrr.data.value?.items ?? []);
const nrrByCategory = computed<Record<string, typeof nrrItems.value>>(() => {
  const g: Record<string, typeof nrrItems.value> = {};
  for (const it of nrrItems.value) (g[String(it.category ?? '—')] ??= []).push(it);
  return g;
});
/**
 * The draft's own requirement entries.
 *
 * This used to read `blueprint/draft` and reach for `document`, which S1 pins to null on purpose
 * (`services/blueprint_reads.py`) — so the block never appeared, and an operator who had just
 * written entries could not see them here. The entry read is what actually answers.
 */
const draftEntries = computed(() => itemsFromEntries((draftEntriesQ.data.value?.entries ?? []) as unknown as Record<string, unknown>[]));

const classOptions: { value: DataClassification; labelKey: string }[] = [
  { value: 'PUBLIC', labelKey: 'inputs.classPublic' },
  { value: 'INTERNAL', labelKey: 'inputs.classInternal' },
  { value: 'CONFIDENTIAL', labelKey: 'inputs.classConfidential' },
  { value: 'RESTRICTED', labelKey: 'inputs.classRestricted' },
  { value: 'PHI_PII', labelKey: 'inputs.classPhi' },
];

// default from the project's last-used class; PHI/PII is never the default (UM-BR-07)
const dataClass = ref<DataClassification>((ui.getDataClass(projectId.value) as DataClassification) || 'INTERNAL');
const phiSheet = ref(false);
let pendingFile: File | null = null;

/**
 * The upload's whole feedback. `xhr.upload.onprogress` reports how much of the *body* left the
 * browser, so it reaches 1.0 on a 403 as happily as on a 201 — a bar that disappears when
 * `uploading` goes false therefore tells the operator nothing about the answer it just got.
 * The outcome stays on screen until the next pick.
 */
type UploadOutcome =
  | { kind: 'idle' }
  | { kind: 'uploading'; name: string; fraction: number }
  | { kind: 'done'; name: string; artifactId: string }
  | { kind: 'failed'; name: string; reason: string };
const outcome = ref<UploadOutcome>({ kind: 'idle' });
const uploading = computed(() => outcome.value.kind === 'uploading');
const progress = computed(() => (outcome.value.kind === 'uploading' ? outcome.value.fraction : 0));
const outcomeName = computed(() => (outcome.value.kind === 'idle' ? '' : outcome.value.name));

const maxSize = computed(() => (limits.data.value as { maxUploadBytes?: number })?.maxUploadBytes ?? undefined);
// `UploadLimitsView` spells this `allowedUploadTypes`; reading `allowedTypes` always produced []
// and the type precheck therefore never ran.
const allowedTypes = computed<string[]>(() => ((limits.data.value as { allowedUploadTypes?: string[] })?.allowedUploadTypes ?? []));

// Upload and parse are answered per workflow (S1 puts `input.upload` / `input.parse` in the project
// action list, not the platform one), so asking the platform list used to grey the button out
// against a real S1 while the mock answered it and the e2e stayed green.
const actions = useAllowedActions(wfId);
const actionAllowed = (action: string, targetId?: string) =>
  resolveAvailability(indexActions(actions.data.value), action, targetId).available;

function precheck(file: File): boolean {
  if (maxSize.value && file.size > maxSize.value) {
    pushWarn(t('inputs.tooLarge', { max: formatBytes(maxSize.value) }));
    return false;
  }
  if (allowedTypes.value.length) {
    const ext = file.name.split('.').pop()?.toLowerCase();
    const type = (file.type || '').toLowerCase();
    const ok = allowedTypes.value.some((a) => a.toLowerCase() === `.${ext}` || a.toLowerCase() === type || a.toLowerCase() === '*/*');
    if (!ok) {
      pushWarn(t('inputs.badType'));
      return false;
    }
  }
  return true;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function onPick(file: File): void {
  if (!precheck(file)) return;
  if (dataClass.value === 'PHI_PII') {
    pendingFile = file;
    phiSheet.value = true; // PHI/PII needs a second confirmation (UM-BR-07)
    return;
  }
  void doUpload(file);
}

async function doUpload(file: File): Promise<void> {
  outcome.value = { kind: 'uploading', name: file.name, fraction: 0 };
  ui.setDataClass(projectId.value, dataClass.value);
  try {
    const res = await uploadInput<Record<string, unknown>>(
      projectId.value,
      file,
      { mediaType: file.type || 'application/octet-stream', dataClassification: dataClass.value, phiDeclared: dataClass.value === 'PHI_PII' },
      (f) => {
        if (outcome.value.kind === 'uploading') outcome.value = { ...outcome.value, fraction: f };
      },
    );
    const artifactId = String(res.artifactId ?? '');
    outcome.value = { kind: 'done', name: file.name, artifactId };
    pushSuccess(t('inputs.uploaded'));
    // Invalidated rather than awaited: the list read used to sit in front of the parse POST, so a
    // slow or failing read decided whether the upload got parsed at all.
    qc.invalidateQueries({ queryKey: qk.inputs(projectId.value) });
    if (!artifactId) return;
    // Parsing is a write of its own: it happens automatically only while allowed-actions says so,
    // and the row keeps a manual 解析 action for when it does not (a failed parse is retried here).
    if (actionAllowed(ACTION.inputParse, artifactId)) {
      parse.mutate({ projectId: projectId.value, artifactId });
    } else {
      pushWarn(t('inputs.parseNotPermitted'));
    }
  } catch (err) {
    // `uploadInput` is a bare XHR promise, not a TanStack mutation, so nothing else in the app
    // hears about a refusal here — without this line the failure was invisible by construction.
    pushError(err);
    outcome.value = { kind: 'failed', name: file.name, reason: reasonOf(err) };
  }
}

/** S1's own sentence when it has one, the console's word for the code otherwise. */
function reasonOf(err: unknown): string {
  if (err instanceof AppError && te(`errors.${err.errorCode}`)) return t(`errors.${err.errorCode}`);
  return (err as Error)?.message ?? String(err);
}

function pick(): void {
  uploader.value?.pickFile();
}

function confirmPhi(): void {
  phiSheet.value = false;
  if (pendingFile) void doUpload(pendingFile);
  pendingFile = null;
}
function cancelPhi(): void {
  phiSheet.value = false;
  pendingFile = null;
  dataClass.value = (ui.getDataClass(projectId.value) as DataClassification) || 'INTERNAL';
}

const list = computed(() => (inputs.data.value?.items ?? []) as Record<string, unknown>[]);
/**
 * The failure codes that mean "the model call did not work" rather than "the data may not go
 * there". The second one arrives from S3 already naming the allowed provider classes; the first
 * arrives as one English sentence about a socket, which is why the row gets a pointer of its own.
 */
const MODEL_FAILURES = new Set(['LLM_TIMEOUT', 'AI_PROVIDER_UNAVAILABLE', 'AI_IN_PROGRESS', 'SCHEMA_ERROR']);
function isModelFailure(row: Record<string, unknown>): boolean {
  return String(row.parseStatus ?? '') === 'FAILED' && MODEL_FAILURES.has(String(row.errorCode ?? ''));
}
/** Only a tabular parse has columns, so only it has a mapping to review. */
function isTabular(parserType: unknown): boolean {
  return parserType === 'CSV' || parserType === 'XLSX';
}
/**
 * The artifact page. The uploaded filename travels as a query because the artifact record does not
 * carry it (`artifact_view()` exposes type, media type, size and hash), and the page needs the name
 * both to title itself and to decide whether the bytes are text it can render.
 */
function openSource(row: Record<string, unknown>): void {
  const artifactId = String(row.artifactId ?? '');
  const name = String(row.filename ?? '');
  router.push(`/artifacts/${artifactId}` + (name ? `?name=${encodeURIComponent(name)}` : ''));
}
function openLog(row: Record<string, unknown>): void {
  router.push(`/projects/${projectId.value}/inputs/${String(row.artifactId)}/llm-log`);
}
/** The parsed list sits on this page; a text input's review starts there, not at column mapping. */
function reviewParsed(): void {
  const target = document.getElementById('nrr');
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  else router.push(`/projects/${projectId.value}/inputs`);
}
function onReviewAnswered(body: T.QuestionnaireAnswerView | undefined): void {
  const open = body?.questions ?? [];
  if (!open.length) {
    pushInfo(t('questions.allAnswered'));
    return;
  }
  pushInfo(
    body?.resumed
      ? t('questions.nextRound', { n: open.length })
      : t('questions.partialSaved', { n: open.length }),
  );
}
function parseTone(s?: string): 'success' | 'danger' | 'warning' | 'muted' | 'info' {
  switch (s) {
    case 'PARSING':
      return 'info';
    case 'PARSED':
      return 'success';
    case 'FAILED':
      return 'danger';
    case 'NEEDS_REVIEW':
      return 'warning';
    case 'POLICY_DENIED':
      return 'danger';
    default:
      return 'muted';
  }
}
</script>

<template>
  <div class="inp">
    <FuiCard :title="t('inputs.title')">
      <FuiActionButton data-bud="upload" :action="ACTION.inputUpload" :workflow-id="wfId" variant="primary" block round :label="t('inputs.upload')" @click="pick()" />
      <FuiUpload ref="uploader" :uploading="uploading" :progress="progress" :accept-hint="t('inputs.autoParseHint')" @pick="onPick" />
      <p v-if="uploading" class="inp__state" role="status">{{ t('inputs.uploadingNow', { name: outcomeName }) }}</p>
      <p v-else-if="outcome.kind === 'done'" class="inp__state inp__state--ok" role="status">
        {{ t('inputs.uploadDone', { name: outcomeName }) }}
      </p>
      <FuiNotice v-else-if="outcome.kind === 'failed'" tone="danger">
        <span role="status">{{ t('inputs.uploadFailed', { name: outcomeName, reason: outcome.reason }) }}</span>
        <FuiButton size="small" :label="t('inputs.upload')" @click="pick()" />
      </FuiNotice>
      <div class="inp__class">
        <span class="inp__label">{{ t('inputs.dataClass') }}</span>
        <FuiRadioGroup v-model="dataClass" direction="horizontal">
          <FuiRadio v-for="o in classOptions" :key="o.value" :name="o.value">{{ t(o.labelKey) }}</FuiRadio>
        </FuiRadioGroup>
      </div>
    </FuiCard>

    <FuiAsync
      :loading="inputs.isLoading.value"
      :error="inputs.isError.value"
      :empty="!list.length"
      :empty-title="t('inputs.title')"
      :empty-hint="t('nrr.empty')"
      :empty-action="t('inputs.upload')"
      @retry="inputs.refetch()"
      @action="pick()"
    >
      <!-- `InputView` names these artifactId / filename / errorCode + errorMessage. Reading id / name
           / parseError left the error text unrenderable and sent the mapping link to /inputs/undefined. -->
      <FuiCard v-for="row in list" :key="String(row.artifactId)" clickable @click="openSource(row)">
        <template #title>{{ row.filename ?? row.artifactId }}</template>
        <template #extra>
          <span :class="{ 'inp__status--link': !!row.parseRunId }" @click.stop="row.parseRunId ? openLog(row) : undefined">
            <FuiStatusTag :tone="parseTone(row.parseStatus as string)" :label="parseStatusLabel(String(row.parseStatus ?? ''))" />
          </span>
        </template>
        <div class="inp__meta">
          <span>{{ t('inputs.dataClass') }}: {{ classificationLabel(String(row.dataClassification ?? '')) }}</span>
          <span v-if="row.errorMessage || row.errorCode" class="inp__err">{{ row.errorMessage ?? row.errorCode }}</span>
        </div>
        <!-- A transport failure says nothing about which provider was tried; POLICY_DENIED already
             comes back naming the classes this document may be sent to, so it needs no second voice. -->
        <p v-if="isModelFailure(row)" class="inp__hint">
          {{ t('inputs.modelFailHint') }}
          <button class="inp__nrr-src" @click="router.push('/governance/providers')">{{ t('governance.providers') }}</button>
        </p>
        <div class="inp__row" @click.stop>
          <FuiButton
            v-if="row.parseStatus === 'NEEDS_REVIEW' && isTabular(row.parserType)"
            size="small"
            :label="t('inputs.reviewMapping')"
            @click="router.push(`/projects/${projectId}/inputs/${row.artifactId}/column-mapping`)"
          />
          <FuiActionButton
            :workflow-id="wfId"
            :action="ACTION.inputParse"
            :target-id="String(row.artifactId)"
            size="small"
            variant="ghost"
            :disabled="row.parseStatus === 'PARSING'"
            :label="t('inputs.parseNow')"
            @click="parse.mutate({ projectId, artifactId: String(row.artifactId) })"
          />
          <FuiButton v-if="row.parseRunId" size="small" :label="t('inputs.viewLlmLog')" @click.stop="openLog(row)" />
          <FuiButton
            v-if="row.parseStatus === 'NEEDS_REVIEW' || row.parseStatus === 'PARSED'"
            size="small"
            :label="t('inputs.reviewParsed')"
            @click.stop="reviewParsed()"
          />
          <FuiButton size="small" :label="t('nrr.viewSource')" @click="openSource(row)" />
        </div>
      </FuiCard>
    </FuiAsync>

    <h3 class="inp__h" id="nrr">{{ t('nrr.title') }}</h3>
    <p class="inp__hint">{{ t('inputs.nrrReviewHint') }}</p>
    <FuiCard v-if="nrrItems.length || reviewQuestions.length || requirementsStage" :title="t('inputs.reviewActions')">
      <p class="inp__hint">{{ t('inputs.reviewActionsHint') }}</p>
      <div v-if="reviewQuestions.length" class="inp__review-questions">
        <QuestionnaireForm
          :questions="reviewQuestions"
          :workflow-id="wfId"
          :stage-id="requirementsStage?.id ?? ''"
          flat
          @submitted="onReviewAnswered"
        />
      </div>
      <FuiNotice v-else-if="!stageQuestions.isLoading.value" tone="info" :text="t('inputs.noReviewQuestions')" />
      <FuiButton
        v-if="requirementsStage"
        size="small"
        variant="primary"
        :label="t('inputs.goRequirements')"
        @click="router.push('/stages/' + requirementsStage.id)"
      />
    </FuiCard>
    <FuiAsync :loading="nrr.isLoading.value" :empty="!nrrItems.length" :empty-title="t('nrr.title')" :empty-hint="t('nrr.empty')">
      <FuiCard v-for="(items, cat) in nrrByCategory" :key="String(cat)" :title="`${t('nrr.category')}: ${cat}`">
        <div v-for="it in items" :key="String(it.businessKey ?? it.statement)" class="inp__nrr">
          <div class="inp__nrr-st">{{ it.statement }}</div>
          <div class="inp__nrr-meta">
            <span>{{ t('nrr.confidence') }}: {{ Math.round(((it.confidence as number) ?? 0) * 100) }}%</span>
            <FuiStatusTag v-if="(it.openQuestions ?? []).length" tone="warning" :label="`${t('nrr.openQuestion')} ${it.openQuestions?.length ?? 0}`" />
            <button v-if="it.artifactId" class="inp__nrr-src" @click="router.push('/artifacts/' + it.artifactId)">{{ t('nrr.viewSource') }}</button>
          </div>
        </div>
      </FuiCard>
    </FuiAsync>

    <template v-if="draftEntries.length">
      <h3 class="inp__h">{{ t('nrr.byDraft') }}</h3>
      <FuiCard v-for="(e, i) in draftEntries" :key="String(e.businessKey ?? i)">
        <template #title>{{ String(e.statement ?? e.businessKey ?? '') }}</template>
        <div class="inp__nrr-meta"><span>{{ e.businessKey ?? '' }}</span></div>
      </FuiCard>
      <FuiButton size="small" variant="primary" :label="t('reqItems.title')" @click="router.push(`/projects/${projectId}/requirements`)" />
    </template>

    <FuiBottomSheet v-model="phiSheet" :title="t('inputs.phiConfirmTitle')">
      <p class="inp__hint">{{ t('inputs.phiConfirmBody') }}</p>
      <template #footer>
        <div class="inp__phi-actions">
          <FuiButton variant="danger" block :label="t('common.confirm')" @click="confirmPhi" />
          <FuiButton block :label="t('common.cancel')" @click="cancelPhi" />
        </div>
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.inp { padding: var(--fui-sp-4); }
.inp__class { margin-top: var(--fui-sp-3); }
.inp__state { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: var(--fui-sp-2) 0 0; }
.inp__state--ok { color: var(--fui-success); }
.inp__label { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.inp__meta { display: flex; justify-content: space-between; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 6px; }
.inp__err { color: var(--fui-danger); }
.inp__row { display: flex; gap: 8px; margin-top: var(--fui-sp-3); flex-wrap: wrap; }
.inp__status--link { cursor: pointer; text-decoration: underline; }
.inp__hint { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.inp__phi-actions { display: flex; gap: 8px; }
.inp__h { font-size: var(--fui-fs-lg); margin: var(--fui-sp-5) 0 var(--fui-sp-2); }
.inp__nrr { padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.inp__nrr-st { font-size: var(--fui-fs-sm); }
.inp__nrr-meta { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 4px; }
.inp__nrr-src { border: 0; background: none; color: var(--fui-primary); font-size: var(--fui-fs-xs); }
.inp__review-questions { display: flex; flex-direction: column; gap: 8px; margin: var(--fui-sp-2) 0; }
</style>
