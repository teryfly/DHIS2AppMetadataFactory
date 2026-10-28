<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiTimeline, FuiAsync, FuiIcon, FuiActionButton, FuiButton } from '@/fui';
import { useProjectWorkflow } from '@/features/common/useProjectWorkflow';
import { useProject, useGatesPending } from '@/api/queries';
import { useRunStage } from '@/api/mutations';
import { stageTypeLabel, stageStatusLabel, toneForStageStatus, toTimelineStatus, deliveryModeLabel, projectTypeLabel } from '@/lib/labels';
import { ACTION } from '@/api/actions';
import type { TimelineItem } from '@/fui/types';
import type { StageView } from '@/api/types';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));

const { workflow, workflowId, stages } = useProjectWorkflow(projectId);
const projQ = useProject(projectId);
const gates = useGatesPending();
const runStage = useRunStage(() => workflowId.value);
const meta = computed(() => projQ.data.value);

const stageList = computed<StageView[]>(() => (stages.data.value?.items ?? []) as StageView[]);
const stageById = computed(() => new Map(stageList.value.map((s) => [s.id, s])));
const AGENT_STAGE_TYPES = new Set(['REQUIREMENTS', 'DOMAIN', 'MAPPING', 'PAGE_DESIGN']);
function readyLabel(stage: StageView): string {
  return AGENT_STAGE_TYPES.has(stage.stageType) ? t('stage.runAgent') : t('stage.nextStep');
}
// The workflow arrives after setup, so the current stage has to be seeded by a watch — the
// timeline's main action must be reachable without an extra expand tap (§6.1 budget 1).
const expandedKey = ref<string | null>(null);
watch(() => workflow.data.value?.currentStageId, (cur) => {
  if (expandedKey.value === null && cur) expandedKey.value = String(cur);
});
// No client-side guess about which stage "looks runnable": the button below asks `stage.run` of
// allowed-actions and shows S1's reason when it says no. The whitelist this replaces listed a
// `PENDING` that StageStatus never has and left out `READY`, which is the state a runnable stage is
// actually in — so the main action disappeared exactly when it was available.
const items = computed<TimelineItem[]>(() =>
  stageList.value.map((s) => ({
    key: s.id,
    title: stageTypeLabel(s.stageType),
    subtitle: stageStatusLabel(s.status),
    // The current stage keeps its own status; forcing RUNNING here hid a stage that was waiting for
    // approval or had failed behind an "in progress" dot.
    status: toTimelineStatus(String(s.status ?? '')),
  })),
);

function waitingGate(stageId: string) {
  return (gates.data.value?.items ?? []).find(
    (gate) => gate.stageId === stageId && gate.status === 'WAITING',
  );
}

function startStage(stageId: string): void {
  runStage.mutate(
    { stageId, rerun: false },
    // The stage page owns the live progress card and run log; landing there is the useful next
    // screen when the button succeeds.
    { onSuccess: () => router.push('/stages/' + stageId) },
  );
}

function openStage(stageId: string, log = false): void {
  router.push('/stages/' + stageId + (log ? '?log=1' : ''));
}

function openGate(stageId: string): void {
  const gate = waitingGate(stageId);
  router.push(gate ? '/gates/' + gate.id : '/todo?kind=gate');
}

const navGroups = computed(() => {
  // Four groups, the same four the desktop rail uses. Fifteen icons in a grid asked the operator to
  // work out which of them were about reading the product, which about shipping it and which about
  // who may read it — and 交付包 next to 开发包, two different packages, is where that guess goes wrong.
  return [
    {
      label: t('nav.groupReview'),
      items: [
        { to: `/projects/${projectId.value}/inputs`, label: t('inputs.title'), icon: 'upgrade' },
        { to: `/projects/${projectId.value}/requirements`, label: t('reqItems.title'), icon: 'edit' },
        { to: `/projects/${projectId.value}/artifacts`, label: t('review.artifacts'), icon: 'bulb-o' },
        { to: `/projects/${projectId.value}/issues`, label: t('review.issues'), icon: 'warning-o' },
        { to: `/projects/${projectId.value}/metadata`, label: t('review.metadata'), icon: 'records' },
        { to: `/projects/${projectId.value}/graph`, label: t('graph.title'), icon: 'share-o' },
        { to: `/projects/${projectId.value}/api`, label: t('api.title'), icon: 'todo-list-o' },
      ],
    },
    {
      label: t('nav.groupDelivery'),
      items: [
        { to: `/projects/${projectId.value}/package`, label: t('pkg.title'), icon: 'gift-o' },
        { to: `/projects/${projectId.value}/handoff`, label: t('handoff.title'), icon: 'description-o' },
        { to: `/workflows/${workflowId.value}/deployments`, label: t('deployments.title'), icon: 'send-gift-o', disabled: !workflowId.value },
        { to: `/projects/${projectId.value}/change-requests`, label: t('changeRequests.title'), icon: 'exchange' },
      ],
    },
    {
      label: t('nav.groupAccess'),
      items: [
        { to: `/projects/${projectId.value}/members`, label: t('admin.membersTitle'), icon: 'friends-o' },
        { to: `/projects/${projectId.value}/grants`, label: t('admin.grantsTitle'), icon: 'lock' },
        { to: `/projects/${projectId.value}/exemptions`, label: t('admin.exemptionsTitle'), icon: 'shield-o' },
      ],
    },
    {
      label: t('nav.groupProject'),
      items: [
        { to: `/projects/${projectId.value}/audit`, label: t('audit.title'), icon: 'eye-o' },
        { to: `/projects/${projectId.value}/settings`, label: t('project.settings'), icon: 'setting-o' },
      ],
    },
  ];
});

</script>

<template>
  <div class="pd">
    <FuiAsync :loading="workflow.isLoading.value" :error="workflow.isError.value" @retry="workflow.refetch()">
      <FuiCard>
        <template #title>{{ meta?.name }}</template>
        <template #extra>
          <FuiStatusTag v-if="meta?.archived" tone="muted" :label="t('projects.archived')" />
          <span v-else class="pd__ver">{{ t('review.versions') }} {{ meta?.currentBlueprintVersion ?? '' }}</span>
        </template>
        <div class="pd__mode">{{ deliveryModeLabel(meta?.deliveryMode) }} · {{ projectTypeLabel(meta?.projectType) }}</div>
        <div class="pd__actions">
          <FuiButton size="mini" @click="router.push(`/projects/${projectId}/settings`)">
            {{ t('project.editProject') }}
          </FuiButton>
          <!-- CP-PRJ-007 orders the two steps, so a live project is offered the archive and only an
               archived one is offered deletion. Both lead to the settings actions, where
               allowed-actions and the typed name decide whether the press can go through. -->
          <FuiButton
            size="mini"
            :variant="meta?.archived ? 'danger' : 'default'"
            @click="router.push(`/projects/${projectId}/settings`)"
          >
            {{ meta?.archived ? t('project.deleteProject') : t('project.archive') }}
          </FuiButton>
        </div>
      </FuiCard>

      <div v-for="group in navGroups" :key="group.label" class="pd__group">
        <span class="pd__group-label">{{ group.label }}</span>
        <div class="pd__nav">
          <button v-for="n in group.items" :key="n.to" class="pd__navbtn" :disabled="n.disabled" @click="router.push(n.to)">
            <FuiIcon :name="n.icon" size="18" /><span>{{ n.label }}</span>
          </button>
        </div>
      </div>

      <h3 class="pd__h">{{ t('project.timeline') }}</h3>
      <FuiCard>
        <FuiTimeline :items="items" :expanded-key="expandedKey" @select="(k) => (expandedKey = expandedKey === k ? null : k)">
          <template #action="{ item }">
            <template v-if="stageById.get(item.key)">
              <FuiActionButton
                v-if="stageById.get(item.key)!.status === 'READY'"
                :workflow-id="workflowId"
                :action="ACTION.stageRun"
                :target-id="item.key"
                size="small"
                variant="primary"
                :label="readyLabel(stageById.get(item.key)!)"
                data-bud="run-stage"
                @click="startStage(item.key)"
              />
              <FuiButton
                v-else-if="stageById.get(item.key)!.status === 'WAITING_APPROVAL'"
                size="small"
                :label="t('todo.goApprove')"
                @click="openGate(item.key)"
              />
              <FuiButton
                v-else-if="stageById.get(item.key)!.status === 'FAILED' || stageById.get(item.key)!.status === 'BLOCKED'"
                size="small"
                :label="t('runLog.open')"
                @click="openStage(item.key, true)"
              />
              <FuiButton
                v-else-if="stageById.get(item.key)!.status === 'WAITING_INPUT'"
                size="small"
                :label="t('questions.answer')"
                @click="openStage(item.key)"
              />
              <FuiButton
                v-else-if="stageById.get(item.key)!.status === 'RUNNING'"
                size="small"
                :label="t('todo.viewStage')"
                @click="openStage(item.key, true)"
              />
            </template>
          </template>
          <template #detail="{ item }">
            <div v-if="stageById.get(item.key)" class="pd__detail">
              <FuiStatusTag :tone="toneForStageStatus(stageById.get(item.key)!.status)" :label="stageStatusLabel(stageById.get(item.key)!.status)" />
              <FuiCard flat>
                <router-link class="pd__link" :to="`/stages/${item.key}`">{{ t('stage.workspace') }} →</router-link>
              </FuiCard>
            </div>
          </template>
        </FuiTimeline>
      </FuiCard>
    </FuiAsync>
  </div>
</template>

<style scoped>
.pd { padding: var(--fui-sp-4); }
.pd__ver { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.pd__mode { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.pd__actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: var(--fui-sp-2); }
.pd__nav { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--fui-sp-2); margin-bottom: var(--fui-sp-2); }
.pd__navbtn { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px 4px; min-height: var(--fui-tap-min); background: var(--fui-surface); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); color: var(--fui-text-secondary); font-size: var(--fui-fs-xs); }
.pd__navbtn:disabled { opacity: 0.4; }
.pd__group { margin-bottom: var(--fui-sp-3); }
.pd__group-label { display: block; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin-bottom: 6px; }
.pd__h { font-size: var(--fui-fs-lg); margin: var(--fui-sp-4) 0 var(--fui-sp-2); }
.pd__detail { display: flex; flex-direction: column; gap: 8px; }
.pd__link { color: var(--fui-primary); text-decoration: none; font-size: var(--fui-fs-sm); }
</style>
