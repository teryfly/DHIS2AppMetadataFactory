<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import {
  FuiActionButton,
  FuiAsync,
  FuiButton,
  FuiCard,
  FuiCheckbox,
  FuiCheckboxGroup,
  FuiConclusionCard,
  FuiField,
  FuiNotice,
  FuiRadio,
  FuiRadioGroup,
  FuiStatusTag,
} from '@/fui';
import { useQuestion, useQuestions } from '@/api/queries';
import { useAnswerQuestions } from '@/api/mutations';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';
import { severityLabel, toneForSeverity } from '@/lib/labels';
import { pushError, pushInfo } from '@/lib/toast';
import type * as T from '@/api/types';

/**
 * The review questionnaire (CP-Q-005): every open question of the stage on one page, each with
 * the model's structured suggestions, one submit for the whole page. The run resumes once after
 * the last blocking answer; if that turn raises new questions the response carries the next round
 * and this page renders it in place.
 */
const { t } = useI18n();
const route = useRoute();
const router = useRouter();

const questionId = computed(() => String(route.params.id ?? ''));
const q = useQuestion(questionId);
const question = computed(() => q.data.value);
const workflowId = computed(() => question.value?.workflowId ?? '');
const projectId = computed(() => question.value?.projectId ?? '');
const stageId = computed(() => question.value?.stageId ?? '');
useWorkflowEvents({ workflowId, projectId, enabled: computed(() => !!workflowId.value) });

const questionsQ = useQuestions(workflowId);
const submit = useAnswerQuestions();

function retryAll(): void {
  void q.refetch();
  void questionsQ.refetch();
}

/** The last round the server handed back. It wins over the cached list until the page is left. */
const liveQuestions = ref<T.QuestionView[] | null>(null);

const stageQuestions = computed(() => {
  const source = liveQuestions.value ?? questionsQ.data.value?.items ?? [];
  return source
    .filter((item) => item.stageId === stageId.value && item.status === 'OPEN')
    .sort((a, b) => {
      const high = (b.severity === 'HIGH' ? 1 : 0) - (a.severity === 'HIGH' ? 1 : 0);
      return high !== 0 ? high : a.text.localeCompare(b.text);
    });
});

interface Draft {
  /** SINGLE: the chosen suggestion id, or OTHER. */
  choice: string;
  /** MULTIPLE: every chosen suggestion id, possibly including OTHER. */
  choices: string[];
  /** The free-text "Other" value. */
  custom: string;
}

const OTHER = '__fui_other__';
const drafts = reactive<Record<string, Draft>>({});

function ensureDraft(id: string): Draft {
  if (!drafts[id]) drafts[id] = { choice: '', choices: [], custom: '' };
  return drafts[id];
}

watch(
  stageQuestions,
  (items) => {
    for (const item of items) ensureDraft(item.id);
  },
  { immediate: true },
);

watch(questionId, () => {
  liveQuestions.value = null;
});

function modeOf(item: T.QuestionView): 'SINGLE' | 'MULTIPLE' {
  return item.answerMode === 'MULTIPLE' ? 'MULTIPLE' : 'SINGLE';
}

function isOther(id: string): boolean {
  const draft = ensureDraft(id);
  return draft.choice === OTHER || draft.choices.includes(OTHER);
}

function answerFor(item: T.QuestionView): { suggestionIds: string[]; customText?: string } | null {
  const draft = ensureDraft(item.id);
  if (modeOf(item) === 'MULTIPLE') {
    const ids = draft.choices.filter((value) => value !== OTHER);
    const custom = draft.choices.includes(OTHER) ? draft.custom.trim() : '';
    if (!ids.length && !custom) return null;
    return { suggestionIds: ids, customText: custom || undefined };
  }
  if (!draft.choice) return null;
  if (draft.choice === OTHER) {
    const custom = draft.custom.trim();
    return custom ? { suggestionIds: [], customText: custom } : null;
  }
  return { suggestionIds: [draft.choice] };
}

function isAnswered(item: T.QuestionView): boolean {
  return answerFor(item) !== null;
}

const answeredCount = computed(() => stageQuestions.value.filter(isAnswered).length);
const unansweredBlocking = computed(
  () => stageQuestions.value.filter((item) => item.blocking && !isAnswered(item)).length,
);
const answers = computed(() =>
  stageQuestions.value
    .map((item) => ({ questionId: item.id, ...(answerFor(item) ?? { suggestionIds: [] }) }))
    .filter((item) => item.suggestionIds.length > 0 || item.customText),
);
const canSubmit = computed(
  () => !!workflowId.value && answers.value.length > 0 && unansweredBlocking.value === 0 && !submit.isPending.value,
);

function submitAll(): void {
  if (!canSubmit.value) return;
  submit.mutate(
    {
      workflowId: workflowId.value,
      body: { answers: answers.value } as T.AnswerQuestionsBody,
    },
    {
      onSuccess: (data) => {
        const body = data as T.QuestionnaireAnswerView | undefined;
        const open = body?.questions ?? [];
        liveQuestions.value = open;
        if (!open.length) {
          pushInfo(t('questions.allAnswered'));
          router.push(stageId.value ? '/stages/' + stageId.value : '/todo');
          return;
        }
        pushInfo(t('questions.nextRound', { n: open.length }));
        globalThis.scrollTo?.({ top: 0, behavior: 'smooth' });
      },
      onError: (error) => pushError(error),
    },
  );
}
</script>

<template>
  <div class="qn">
    <FuiAsync
      :loading="q.isLoading.value || questionsQ.isLoading.value"
      :error="q.isError.value || questionsQ.isError.value"
      @retry="retryAll"
    >
      <template v-if="question">
        <FuiConclusionCard
          :tone="stageQuestions.length ? 'primary' : 'success'"
          :status-text="t('questions.title')"
          :conclusion="t('questions.questionnaireTitle')"
          :impact="t('questions.answeredCount', { done: answeredCount, total: stageQuestions.length })"
        >
          <template #summary>
            <p class="qn__hint">{{ t('questions.questionnaireHint') }}</p>
          </template>
        </FuiConclusionCard>

        <FuiNotice
          v-if="!stageQuestions.length"
          tone="info"
          :text="t('questions.empty')"
        />
        <FuiButton
          v-if="!stageQuestions.length"
          block
          :label="t('questions.backToStage')"
          @click="router.push(stageId ? '/stages/' + stageId : '/todo')"
        />

        <template v-else>
          <FuiNotice
            v-if="unansweredBlocking"
            tone="danger"
            :text="t('questions.missingBlockingHint', { n: unansweredBlocking })"
          />

          <FuiCard v-for="(item, index) in stageQuestions" :key="item.id">
            <template #title>{{ index + 1 }}. {{ item.text }}</template>
            <div class="qn__meta">
              <FuiStatusTag :tone="toneForSeverity(String(item.severity))" :label="severityLabel(String(item.severity))" />
              <FuiStatusTag v-if="item.blocking" tone="danger" :label="t('questions.blockingTag')" />
              <FuiStatusTag v-else tone="muted" :label="t('questions.optional')" />
              <span class="qn__mode">{{ modeOf(item) === 'MULTIPLE' ? t('questions.multipleHint') : t('questions.singleHint') }}</span>
            </div>
            <p v-if="item.affects?.length" class="qn__affects">
              {{ t('questions.affects') }}: {{ item.affects.join(', ') }}
            </p>

            <FuiRadioGroup v-if="modeOf(item) === 'SINGLE'" v-model="ensureDraft(item.id).choice">
              <FuiRadio v-for="s in item.suggestions" :key="s.id" :name="s.id">
                <span class="qn__option">
                  <span class="qn__option-text">
                    {{ s.text }}
                    <FuiStatusTag v-if="s.recommended" tone="success" :label="t('questions.recommended')" />
                    <FuiStatusTag v-if="s.changesUid" tone="warning" :label="t('questions.changesUid')" />
                  </span>
                  <span v-if="s.rationale" class="qn__why">{{ s.rationale }}</span>
                  <span v-if="s.regenerates?.length" class="qn__why">{{ t('questions.regenerates', { n: s.regenerates.length }) }}</span>
                </span>
              </FuiRadio>
              <FuiRadio :name="OTHER">
                <span class="qn__option-text">{{ t('questions.other') }}</span>
              </FuiRadio>
            </FuiRadioGroup>

            <FuiCheckboxGroup v-else v-model="ensureDraft(item.id).choices">
              <FuiCheckbox v-for="s in item.suggestions" :key="s.id" :name="s.id">
                <span class="qn__option">
                  <span class="qn__option-text">
                    {{ s.text }}
                    <FuiStatusTag v-if="s.recommended" tone="success" :label="t('questions.recommended')" />
                    <FuiStatusTag v-if="s.changesUid" tone="warning" :label="t('questions.changesUid')" />
                  </span>
                  <span v-if="s.rationale" class="qn__why">{{ s.rationale }}</span>
                  <span v-if="s.regenerates?.length" class="qn__why">{{ t('questions.regenerates', { n: s.regenerates.length }) }}</span>
                </span>
              </FuiCheckbox>
              <FuiCheckbox :name="OTHER">
                <span class="qn__option-text">{{ t('questions.other') }}</span>
              </FuiCheckbox>
            </FuiCheckboxGroup>

            <FuiField
              v-if="isOther(item.id)"
              v-model="ensureDraft(item.id).custom"
              type="textarea"
              rows="3"
              :label="t('questions.otherLabel')"
              :placeholder="t('questions.otherPlaceholder')"
            />
            <p v-if="item.blocking && !isAnswered(item)" class="qn__missing">
              {{ t('questions.missingBlocking') }}
            </p>
          </FuiCard>

          <FuiNotice v-if="unansweredBlocking" tone="danger" :text="t('questions.missingBlockingHint', { n: unansweredBlocking })" />
          <div class="qn__actions">
            <FuiActionButton
              :workflow-id="workflowId"
              action="question.answer"
              :target-id="stageId || null"
              block
              variant="primary"
              :disabled="!canSubmit"
              :loading="submit.isPending.value"
              :label="t('questions.submitAll')"
              @click="submitAll"
            />
            <p class="qn__hint">{{ t('questions.submitAllHint') }}</p>
          </div>
        </template>
      </template>
    </FuiAsync>
  </div>
</template>

<style scoped>
.qn { padding: var(--fui-sp-4); padding-bottom: var(--fui-sp-10); }
.qn__hint { margin: 8px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); line-height: 1.6; }
.qn__meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px; }
.qn__mode { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.qn__affects { margin: 0 0 8px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); overflow-wrap: anywhere; }
.qn__option { display: flex; flex-direction: column; gap: 2px; padding: 4px 0; }
.qn__option-text { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: var(--fui-fs-sm); font-weight: 600; }
.qn__why { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); line-height: 1.5; }
.qn__missing { margin: 8px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-danger); }
.qn__actions { position: sticky; bottom: 0; background: var(--fui-bg); padding: var(--fui-sp-3) 0; }
</style>
