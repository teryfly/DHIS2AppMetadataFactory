<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiFilterBar, FuiButton } from '@/fui';
import { useWorkflowOfProject, useValidationIssues, useStages } from '@/api/queries';
import { useExplainArtifact } from '@/api/mutations';
import { severityLabel, stageTypeLabel, toneForSeverity } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflowOfProject(projectId);
const workflowId = computed(() => workflow.data.value?.id ?? '');
const level = ref('');
const issues = useValidationIssues(workflowId, () => (level.value ? { level: level.value } : undefined));
const stages = useStages(workflowId);
const explain = useExplainArtifact();

const list = computed(() => (issues.data.value?.items ?? []) as Record<string, unknown>[]);

// `IssueView` spells the body `description` and the origin `sourceStage` — a stage *type*. Reading
// `message` / `sourceStageId` meant the card rendered no text and the jump button never appeared.
// The counts next to each option are of the page currently loaded, not of the whole set; saying so
// keeps a filtered list from reading like a total.
const options = computed(() => [
  { value: '', label: t('common.all'), count: list.value.length },
  { value: 'BLOCKING', label: t('severity.BLOCKING') },
  { value: 'ERROR', label: t('severity.ERROR') },
  { value: 'WARNING', label: t('severity.WARNING') },
  { value: 'INFO', label: t('severity.INFO') },
]);

function stageIdFor(sourceStage: unknown): string {
  const type = String(sourceStage ?? '');
  if (!type) return '';
  return String((stages.data.value?.items ?? []).find((s) => String(s.stageType ?? '') === type)?.id ?? '');
}

function openExplanation(artifactId: string): void {
  void router.push(`/artifacts/${encodeURIComponent(artifactId)}`);
}

function doExplain(issue: Record<string, unknown>): void {
  const artifactId = String(issue.artifactId ?? '');
  if (!artifactId) return;
  explain.mutate(
    { artifactId, body: { projectId: projectId.value } },
    {
      // The answer is an artifact id; dropping it left the operator with a toast and nothing to read.
      onSuccess: (res) => {
        const explained = String((res as { explanationArtifactId?: string }).explanationArtifactId ?? '');
        if (explained) openExplanation(explained);
      },
    },
  );
}
</script>

<template>
  <div class="vi">
    <FuiFilterBar v-model="level" :options="options" />
    <FuiAsync
      :loading="issues.isLoading.value"
      :error="issues.isError.value"
      :empty="!list.length"
      :empty-title="t('review.issues')"
      @retry="issues.refetch()"
    >
      <FuiCard v-for="it in list" :key="String(it.issueId ?? `${it.ruleId}-${it.businessKey}`)">
        <template #title><FuiStatusTag :tone="toneForSeverity(String(it.level))" :label="severityLabel(String(it.level))" /></template>
        <div class="vi__msg">{{ it.description ?? '' }}</div>
        <div v-if="it.remediation" class="vi__fix">{{ t('review.remediation') }}：{{ it.remediation }}</div>
        <div class="vi__meta">
          <span v-if="it.ruleId">{{ it.ruleId }}</span>
          <span v-if="it.businessKey">{{ it.businessKey }}</span>
        </div>
        <div class="vi__row">
          <FuiButton
            v-if="stageIdFor(it.sourceStage)"
            size="small"
            :label="`${t('review.jumpSource')}${it.sourceStage ? ` · ${stageTypeLabel(String(it.sourceStage))}` : ''}`"
            @click="router.push(`/stages/${stageIdFor(it.sourceStage)}`)"
          />
          <FuiButton v-else-if="it.sourceStage" size="small" :label="stageTypeLabel(String(it.sourceStage))" disabled />
          <FuiButton
            size="small"
            :label="t('review.explain')"
            :loading="explain.isPending.value"
            :disabled="!it.artifactId"
            @click="doExplain(it)"
          />
        </div>
      </FuiCard>
    </FuiAsync>
  </div>
</template>

<style scoped>
.vi { padding: var(--fui-sp-2) 0; }
.vi__msg { font-size: var(--fui-fs-sm); margin-top: 6px; }
.vi__fix { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 4px; }
.vi__meta { display: flex; gap: 12px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 4px; }
.vi__row { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
</style>
