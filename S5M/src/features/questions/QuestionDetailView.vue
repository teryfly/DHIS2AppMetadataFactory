<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiAsync, FuiButton, FuiConclusionCard, FuiNotice } from '@/fui';
import { useQuestion, useQuestions } from '@/api/queries';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';
import { pushInfo } from '@/lib/toast';
import type * as T from '@/api/types';
import QuestionnaireForm from './QuestionnaireForm.vue';

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

watch(questionId, () => {
  liveQuestions.value = null;
});

function onSubmitted(body: T.QuestionnaireAnswerView | undefined): void {
  const open = body?.questions ?? [];
  liveQuestions.value = open;
  if (!open.length) {
    pushInfo(t('questions.allAnswered'));
    router.push(stageId.value ? '/stages/' + stageId.value : '/todo');
    return;
  }
  pushInfo(
    body?.resumed
      ? t('questions.nextRound', { n: open.length })
      : t('questions.partialSaved', { n: open.length }),
  );
  globalThis.scrollTo?.({ top: 0, behavior: 'smooth' });
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

        <QuestionnaireForm
          v-else
          :questions="stageQuestions"
          :workflow-id="workflowId"
          :stage-id="stageId"
          sticky-actions
          @submitted="onSubmitted"
        />
      </template>
    </FuiAsync>
  </div>
</template>

<style scoped>
.qn { padding: var(--fui-sp-4); padding-bottom: var(--fui-sp-10); }
.qn__hint { margin: 8px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); line-height: 1.6; }
</style>
