<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  FuiActionButton,
  FuiButton,
  FuiCheckbox,
  FuiCheckboxGroup,
  FuiField,
  FuiNotice,
  FuiRadio,
  FuiRadioGroup,
  FuiStatusTag,
} from '@/fui';
import { useAnswerQuestions } from '@/api/mutations';
import { severityLabel, toneForSeverity } from '@/lib/labels';
import { pushError } from '@/lib/toast';
import type * as T from '@/api/types';
import { OTHER, modeOf, useAnswerDrafts } from './answerDrafts';

const props = withDefaults(
  defineProps<{
    questions: T.QuestionView[];
    workflowId?: string;
    stageId?: string | null;
    stickyActions?: boolean;
    flat?: boolean;
  }>(),
  { workflowId: '', stageId: null, stickyActions: false, flat: false },
);

const emit = defineEmits<{ (e: 'submitted', body: T.QuestionnaireAnswerView | undefined): void }>();

const { t } = useI18n();
const submit = useAnswerQuestions();
const {
  draftFor,
  answerForQuestion,
  answered,
  unansweredBlocking,
  answers,
  acceptAllRecommended,
} = useAnswerDrafts(() => props.questions);

const unansweredBlockingCount = computed(() => unansweredBlocking.value.length);
const canSubmit = computed(() => !!props.workflowId && answers.value.length > 0 && !submit.isPending.value);
const targetId = computed(() => props.stageId || props.questions[0]?.stageId || null);

function isOther(id: string): boolean {
  const draft = draftFor(id);
  return draft.choice === OTHER || draft.choices.includes(OTHER);
}

function isAnswered(item: T.QuestionView): boolean {
  return answerForQuestion(item) !== null;
}

function hasModelRecommendation(item: T.QuestionView): boolean {
  return (item.suggestions ?? []).some((suggestion) => suggestion.recommended);
}

/** The model's own flag when present; otherwise the first option is the console's fallback. */
function isRecommendedSuggestion(item: T.QuestionView, suggestion: T.SuggestionView, index: number): boolean {
  return Boolean(suggestion.recommended) || (!hasModelRecommendation(item) && index === 0);
}

function recommendationLabel(item: T.QuestionView): string {
  return hasModelRecommendation(item) ? t('questions.recommended') : t('questions.recommendedDefault');
}

function submitAll(): void {
  if (!canSubmit.value) return;
  submit.mutate(
    {
      workflowId: props.workflowId,
      body: { answers: answers.value } as T.AnswerQuestionsBody,
    },
    {
      onSuccess: (data) => emit('submitted', data as T.QuestionnaireAnswerView | undefined),
      onError: (error) => pushError(error),
    },
  );
}
</script>

<template>
  <div class="qf">
    <p class="qf__count">{{ t('questions.answeredCount', { done: answered.length, total: questions.length }) }}</p>
    <FuiNotice
      v-if="unansweredBlockingCount"
      tone="danger"
      :text="t('questions.missingBlockingHint', { n: unansweredBlockingCount })"
    />

    <section v-for="(item, index) in questions" :key="item.id" class="qf__item" :class="{ 'qf__item--flat': flat }">
      <h3 class="qf__title">{{ index + 1 }}. {{ item.text }}</h3>
      <div class="qf__meta">
        <FuiStatusTag :tone="toneForSeverity(String(item.severity))" :label="severityLabel(String(item.severity))" />
        <FuiStatusTag v-if="item.blocking" tone="danger" :label="t('questions.blockingTag')" />
        <FuiStatusTag v-else tone="muted" :label="t('questions.optional')" />
        <span class="qf__mode">{{ modeOf(item) === 'MULTIPLE' ? t('questions.multipleHint') : t('questions.singleHint') }}</span>
      </div>
      <p v-if="item.affects?.length" class="qf__affects">
        {{ t('questions.affects') }}: {{ item.affects.join(', ') }}
      </p>
      <p v-if="!(item.suggestions ?? []).length" class="qf__no-suggestions">
        {{ t('questions.noSuggestionsHint') }}
      </p>

      <FuiRadioGroup v-if="modeOf(item) === 'SINGLE'" v-model="draftFor(item.id).choice">
        <FuiRadio v-for="(suggestion, suggestionIndex) in item.suggestions ?? []" :key="suggestion.id" :name="suggestion.id">
          <span class="qf__option">
            <span class="qf__option-text">
              {{ suggestion.text }}
              <FuiStatusTag
                v-if="isRecommendedSuggestion(item, suggestion, suggestionIndex)"
                tone="success"
                :label="recommendationLabel(item)"
              />
              <FuiStatusTag v-if="suggestion.changesUid" tone="warning" :label="t('questions.changesUid')" />
            </span>
            <span v-if="suggestion.rationale" class="qf__why">{{ suggestion.rationale }}</span>
            <span v-if="suggestion.regenerates?.length" class="qf__why">
              {{ t('questions.regenerates', { n: suggestion.regenerates.length }) }}
            </span>
          </span>
        </FuiRadio>
        <FuiRadio :name="OTHER">
          <span class="qf__option-text">{{ t('questions.other') }}</span>
        </FuiRadio>
      </FuiRadioGroup>

      <FuiCheckboxGroup v-else v-model="draftFor(item.id).choices">
        <FuiCheckbox v-for="(suggestion, suggestionIndex) in item.suggestions ?? []" :key="suggestion.id" :name="suggestion.id">
          <span class="qf__option">
            <span class="qf__option-text">
              {{ suggestion.text }}
              <FuiStatusTag
                v-if="isRecommendedSuggestion(item, suggestion, suggestionIndex)"
                tone="success"
                :label="recommendationLabel(item)"
              />
              <FuiStatusTag v-if="suggestion.changesUid" tone="warning" :label="t('questions.changesUid')" />
            </span>
            <span v-if="suggestion.rationale" class="qf__why">{{ suggestion.rationale }}</span>
            <span v-if="suggestion.regenerates?.length" class="qf__why">
              {{ t('questions.regenerates', { n: suggestion.regenerates.length }) }}
            </span>
          </span>
        </FuiCheckbox>
        <FuiCheckbox :name="OTHER">
          <span class="qf__option-text">{{ t('questions.other') }}</span>
        </FuiCheckbox>
      </FuiCheckboxGroup>

      <FuiField
        v-if="isOther(item.id)"
        v-model="draftFor(item.id).custom"
        type="textarea"
        rows="3"
        :label="t('questions.otherLabel')"
        :placeholder="t('questions.otherPlaceholder')"
      />
      <p v-if="item.blocking && !isAnswered(item)" class="qf__missing">
        {{ t('questions.missingBlocking') }}
      </p>
    </section>

    <div class="qf__actions" :class="{ 'qf__actions--sticky': stickyActions }">
      <FuiButton block :label="t('questions.acceptAllRecommended')" :disabled="!questions.length" @click="acceptAllRecommended" />
      <FuiActionButton
        :workflow-id="workflowId"
        action="question.answer"
        :target-id="targetId"
        block
        variant="primary"
        :disabled="!canSubmit"
        :loading="submit.isPending.value"
        :label="t('questions.submitAll')"
        @click="submitAll"
      />
      <p class="qf__hint">{{ t('questions.submitAllHint') }}</p>
    </div>
  </div>
</template>

<style scoped>
.qf { display: flex; flex-direction: column; gap: var(--fui-sp-3); }
.qf__count { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.qf__item { background: var(--fui-surface); border-radius: var(--fui-radius-card); box-shadow: var(--fui-shadow-1); padding: var(--fui-sp-4); }
.qf__item--flat { box-shadow: none; border: 1px solid var(--fui-border); padding: var(--fui-sp-3); }
.qf__title { margin: 0 0 8px; font-size: var(--fui-fs-md); font-weight: 600; line-height: 1.5; overflow-wrap: anywhere; }
.qf__meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px; }
.qf__mode { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.qf__affects { margin: 0 0 8px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); overflow-wrap: anywhere; }
.qf__no-suggestions { margin: 0 0 8px; font-size: var(--fui-fs-xs); color: var(--fui-warning); }
.qf__option { display: flex; flex-direction: column; gap: 2px; padding: 4px 0; }
.qf__option-text { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: var(--fui-fs-sm); font-weight: 600; }
.qf__why { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); line-height: 1.5; }
.qf__missing { margin: 8px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-danger); }
.qf__actions { display: flex; flex-direction: column; gap: var(--fui-sp-2); }
.qf__actions--sticky { position: sticky; bottom: 0; background: var(--fui-bg); padding: var(--fui-sp-3) 0; }
.qf__hint { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); line-height: 1.6; }
</style>
