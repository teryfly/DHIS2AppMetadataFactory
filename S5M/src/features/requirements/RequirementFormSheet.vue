<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiBottomSheet, FuiButton, FuiActionButton, FuiField, FuiNotice, FuiSegmented, FuiStatusTag } from '@/fui';
import { ACTION } from '@/api/actions';
import { AppError } from '@/api/errors';
import { nrrPriorityLabel, entryStatusLabel, severityLabel } from '@/lib/labels';
import type { RequirementEdit, RequirementItem, RequirementRules } from './requirementOps';
import { requirementProblems } from './requirementOps';

const props = defineProps<{
  modelValue: boolean;
  /** `null` opens the sheet to create; an item opens it to edit that entry. */
  item: RequirementItem | null;
  /** The draft as it was read, so a duplicate key is caught before the write. */
  read: RequirementItem[];
  rules: RequirementRules;
  stageId: string;
  workflowId: string;
  saving: boolean;
  failure: unknown;
}>();

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'save', edit: RequirementEdit): void }>();

const { t } = useI18n();

const creating = computed(() => !props.item);

/**
 * The six fields this screen edits. Everything else an entry carries (`actors`, a
 * `source.artifactId` the parse pipeline recorded) stays in the draft untouched — the ops builder
 * merges into the read item rather than replacing it.
 */
const form = reactive({
  businessKey: '',
  statement: '',
  rationale: '',
  priority: 'MUST',
  status: 'CANDIDATE',
  confidence: '0.9',
  note: '',
  questions: [] as { question: string; severity: string }[],
});

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    const from = props.item;
    form.businessKey = String(from?.businessKey ?? '');
    form.statement = String(from?.statement ?? '');
    form.rationale = String(from?.rationale ?? '');
    form.priority = String(from?.priority ?? props.rules.priorities[0] ?? 'MUST');
    form.status = String(from?.status ?? props.rules.statuses[0] ?? 'CANDIDATE');
    form.confidence = from?.confidence === undefined || from?.confidence === null ? '0.9' : String(from.confidence);
    form.note = String(from?.source?.note ?? '');
    form.questions = (from?.openQuestions ?? []).map((q) => ({ question: String(q.question ?? ''), severity: String(q.severity ?? 'MEDIUM') }));
  },
);

const priorityOptions = computed(() => props.rules.priorities.map((value) => ({ value, label: nrrPriorityLabel(value) })));
const statusOptions = computed(() => props.rules.statuses.map((value) => ({ value, label: entryStatusLabel(value) })));
const severityOptions = computed(() => props.rules.severities.map((value) => ({ value, label: severityLabel(value) })));

function draftItem(): RequirementItem {
  const base = (props.item ? { ...props.item } : {}) as RequirementItem;
  const confidence = Number(form.confidence);
  return {
    ...base,
    businessKey: form.businessKey.trim(),
    statement: form.statement.trim(),
    source: { ...(base.source as Record<string, unknown> | undefined), note: form.note.trim() },
    rationale: form.rationale.trim() || undefined,
    priority: form.priority,
    status: form.status,
    confidence: Number.isFinite(confidence) ? confidence : undefined,
    openQuestions: form.questions
      .map((q) => ({ question: q.question.trim(), severity: q.severity }))
      .filter((q) => q.question),
  };
}

const problems = computed(() =>
  requirementProblems(draftItem(), props.read, props.rules, props.item ? String(props.item.businessKey) : null, t),
);
const canSubmit = computed(() => problems.value.length === 0 && !!form.statement.trim());

/** An entry's key is its identity across versions, so editing it is a different operation entirely. */
const keyLocked = computed(() => !creating.value);
const serverError = computed(() =>
  props.failure instanceof AppError ? props.failure.message : String((props.failure as Error | null)?.message ?? ''),
);

function save(): void {
  if (problems.value.length) return;
  const next = draftItem();
  emit('save', creating.value ? { kind: 'create', item: next } : { kind: 'update', businessKey: String(props.item?.businessKey), item: next });
}

function addQuestion(): void {
  form.questions = [...form.questions, { question: '', severity: props.rules.severities[1] ?? 'MEDIUM' }];
}
function patchQuestion(index: number, change: Partial<{ question: string; severity: string }>): void {
  form.questions = form.questions.map((q, i) => (i === index ? { ...q, ...change } : q));
}
function removeQuestion(index: number): void {
  form.questions = form.questions.filter((_, i) => i !== index);
}

const close = () => emit('update:modelValue', false);
</script>

<template>
  <FuiBottomSheet :model-value="modelValue" :title="creating ? t('reqItems.add') : t('reqItems.edit')" @update:model-value="(v: boolean) => emit('update:modelValue', v)">
    <FuiNotice v-if="serverError" tone="danger" :text="serverError" />

    <FuiField
      v-model="form.businessKey"
      :label="t('reqItems.key')"
      :placeholder="'req.delivery.call'"
      :readonly="keyLocked"
      :help="keyLocked ? t('reqItems.keyImmutable') : t('reqItems.keyHelp')"
    />
    <FuiField v-model="form.statement" :label="t('reqItems.statement')" type="textarea" rows="4" :maxlength="rules.statementMax" />
    <FuiField v-model="form.rationale" :label="t('reqItems.rationale')" type="textarea" rows="2" />

    <div class="rfs__group">
      <span class="rfs__legend">{{ t('reqItems.priority') }}</span>
      <FuiSegmented v-model="form.priority" :options="priorityOptions" :label="t('reqItems.priority')" />
    </div>
    <div class="rfs__group">
      <span class="rfs__legend">{{ t('reqItems.status') }}</span>
      <FuiSegmented v-model="form.status" :options="statusOptions" :label="t('reqItems.status')" />
    </div>

    <FuiField
      v-model="form.confidence"
      :label="t('reqItems.confidence')"
      type="number"
      step="0.05"
      :help="t('reqItems.confidenceRange', { min: rules.confidenceMin, max: rules.confidenceMax })"
    />
    <FuiField v-model="form.note" :label="t('reqItems.sourceNote')" type="textarea" rows="2" :maxlength="rules.noteMax" :help="t('reqItems.sourceNoteHelp')" />

    <div class="rfs__group">
      <div class="rfs__legendrow">
        <span class="rfs__legend">{{ t('reqItems.openQuestions') }}</span>
        <FuiButton size="mini" variant="ghost" :label="t('reqItems.addQuestion')" @click="addQuestion" />
      </div>
      <div v-for="(q, i) in form.questions" :key="i" class="rfs__question">
        <FuiField :model-value="q.question" :label="t('reqItems.questionText')" @update:model-value="(v: string) => patchQuestion(i, { question: v })" />
        <FuiSegmented small :model-value="q.severity" :options="severityOptions" :label="t('reqItems.questionSeverity')" @update:model-value="(v: string) => patchQuestion(i, { severity: v })" />
        <FuiButton size="mini" variant="ghost" :label="t('reqItems.removeQuestion')" @click="removeQuestion(i)" />
      </div>
      <p v-if="!form.questions.length" class="rfs__muted">{{ t('reqItems.noQuestions') }}</p>
    </div>

    <p v-for="p in problems" :key="p" class="rfs__err">{{ p }}</p>

    <template #footer>
      <div class="rfs__foot">
        <FuiButton block :label="t('common.cancel')" @click="close" />
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.patchSubmit"
          :target-id="stageId"
          variant="primary"
          block
          :label="t('common.submit')"
          :disabled="!canSubmit"
          :loading="saving"
          @click="save"
        />
      </div>
    </template>

    <p v-if="item?.source?.artifactId" class="rfs__prov">
      <FuiStatusTag tone="info" :label="t('reqItems.fromParse')" />
      <code class="rfs__code">{{ String(item.source.artifactId) }}</code>
      <span v-if="item.source.location">{{ String(item.source.location) }}</span>
    </p>
  </FuiBottomSheet>
</template>

<style scoped>
.rfs__group { margin: var(--fui-sp-3) 0; }
.rfs__legend { display: block; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin-bottom: 6px; }
.rfs__legendrow { display: flex; align-items: center; justify-content: space-between; }
.rfs__question { border-top: 1px solid var(--fui-border); padding: 6px 0; }
.rfs__err { color: var(--fui-danger); font-size: var(--fui-fs-sm); margin: 4px 0; }
.rfs__muted { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); }
.rfs__foot { display: flex; gap: 8px; }
.rfs__prov { display: flex; align-items: center; gap: 8px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: var(--fui-sp-3); }
.rfs__code { font-size: var(--fui-fs-xs); word-break: break-all; }
</style>
