<script setup lang="ts">
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton, FuiActionButton } from '@/fui';
import { useWorkflow, useProject, useDeploymentRuns } from '@/api/queries';
import { useCreateRun } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { envClassLabel, stageStatusLabel, toneForRunStatus } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const workflowId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflow(workflowId);
const projectId = computed(() => workflow.data.value?.projectId ?? '');
const project = useProject(projectId);
const runs = useDeploymentRuns(workflowId);

const createRun = useCreateRun();

const targetEnvs = computed(() => [...(project.data.value?.targetEnvironments ?? [])].sort((a, b) => a.promotionOrder - b.promotionOrder));
const projVersion = computed(() => project.data.value?.currentBlueprintVersion ?? '');
const runTime = (r: { startedAt?: string | null; completedAt?: string | null }): number => Date.parse(String(r.startedAt ?? r.completedAt ?? '')) || 0;
const latestRunFor = (envKey: string) => (runs.data.value?.items ?? []).filter((r) => r.environmentKey === envKey).sort((a, b) => runTime(b) - runTime(a))[0];

function onCreate(envKey: string): void {
  createRun.mutate({ workflowId: workflowId.value, environmentKey: envKey });
}
</script>

<template>
  <div class="dp">
    <FuiAsync :loading="project.isLoading.value || workflow.isLoading.value" :empty="!targetEnvs.length" :empty-title="t('deployments.title')">
      <template v-for="(e, i) in targetEnvs" :key="e.environmentKey">
        <FuiCard>
          <template #title>{{ e.environmentKey }}<span class="dp__order">· #{{ e.promotionOrder }}</span></template>
          <template #extra>
            <div class="dp__tags">
              <FuiStatusTag v-if="e.isDesignReference" tone="primary" :label="t('deployments.designRef')" />
              <FuiStatusTag tone="info" :label="envClassLabel(e.environmentClass)" />
              <FuiStatusTag v-if="latestRunFor(e.environmentKey)" :tone="toneForRunStatus(latestRunFor(e.environmentKey)!.status)" :label="stageStatusLabel(latestRunFor(e.environmentKey)!.status)" />
            </div>
          </template>
          <div class="dp__meta">
            <span>{{ t('review.versions') }}: {{ projVersion }}</span>
            <FuiButton v-if="latestRunFor(e.environmentKey)" size="small" :label="t('deployments.openConsole')" @click="router.push(`/runs/${latestRunFor(e.environmentKey)!.id}`)" />
          </div>
          <div class="dp__create">
            <FuiActionButton :workflow-id="workflowId" :action="ACTION.deploymentCreate" :target-id="e.environmentKey" size="small" :label="t('deployments.createRun')" @click="onCreate(e.environmentKey)" />
          </div>
        </FuiCard>
        <div v-if="i < targetEnvs.length - 1" class="dp__arrow">↓ {{ t('project.envOrderHint') }}</div>
      </template>
    </FuiAsync>
  </div>
</template>

<style scoped>
.dp { padding: var(--fui-sp-4); }
.dp__order { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); font-weight: 400; margin-left: 6px; }
.dp__tags { display: flex; gap: 6px; flex-wrap: wrap; }
.dp__meta { display: flex; justify-content: space-between; align-items: center; margin: 6px 0; }
.dp__create { margin-top: 8px; }
.dp__arrow { text-align: center; color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin: 4px 0; }
</style>
