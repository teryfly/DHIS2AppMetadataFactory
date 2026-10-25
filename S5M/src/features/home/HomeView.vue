<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiButton, FuiAsync, FuiActionButton } from '@/fui';
import { useProjects, useGatesPending, useRecentRuns } from '@/api/queries';
import { ACTION } from '@/api/actions';
import StartCard from '@/features/start/StartCard.vue';
import { usePrefsStore } from '@/stores/prefs';
import { fmtRelTime } from '@/lib/time';
import { milestoneLabel, stageStatusLabel, stageTypeLabel, toneForRunStatus, toneForStageStatus } from '@/lib/labels';
import type { ProjectView, ProjectStageView } from '@/api/types';

const { t } = useI18n();
const router = useRouter();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));

const projects = useProjects(false);
const gates = useGatesPending();
const runs = useRecentRuns();

const items = computed<ProjectView[]>(() => projects.data.value?.items ?? []);
const currentStage = (p: ProjectView): ProjectStageView | undefined => p.stages?.find((s) => s.id === p.currentStageId);
const runningProjects = computed(() => items.value.filter((p) => p.workflowStatus === 'ACTIVE' && !p.archived));
const recentDeploys = computed(() => (runs.data.value?.items ?? []).slice(0, 5));
const pendingCount = computed(() => gates.data.value?.total ?? 0);
</script>

<template>
  <div class="home">
    <section class="home__quick">
      <!-- UM-SET-004: "新建项目" is S1's to allow. An ungated entry meant three filled-in steps and
           then a 403 for a caller the platform answer already refused. -->
      <FuiActionButton
        variant="primary"
        platform
        block
        round
        :action="ACTION.projectCreate"
        :label="t('home.newProject')"
        @click="router.push('/projects/new')"
      />
      <div class="home__quick-row">
        <FuiButton block :label="t('home.goStart')" @click="router.push('/start')" />
        <FuiButton block :label="t('tabs.messages')" @click="router.push('/notifications')" />
        <FuiButton block :label="t('projects.title')" @click="router.push('/projects')" />
      </div>
    </section>

    <StartCard />

    <FuiCard v-if="pendingCount > 0" clickable :title="t('todo.title')" @click="router.push('/todo')">
      <div class="home__todo-num">{{ t('todo.count', { n: pendingCount }) }}</div>
    </FuiCard>

    <h3 class="home__h">{{ t('home.myProjects') }}</h3>
    <FuiAsync :loading="projects.isLoading.value" :empty="!items.length" :empty-title="t('projects.title')" :empty-hint="t('home.noProjects')" :empty-action="t('home.newProject')" @action="router.push('/projects/new')">
      <FuiCard v-for="p in items.slice(0, 6)" :key="p.id" clickable :title="p.name" @click="router.push(`/projects/${p.id}`)">
        <template #extra><FuiStatusTag v-if="p.archived" tone="muted" :label="t('projects.archived')" /></template>
        <FuiStatusTag
          v-if="currentStage(p)"
          :tone="toneForStageStatus(currentStage(p)!.status)"
          :label="stageTypeLabel(currentStage(p)!.stageType) + ' · ' + stageStatusLabel(currentStage(p)!.status)"
        />
        <div class="home__meta"><span>{{ milestoneLabel(p.milestoneStatus) }}</span><span>{{ fmtRelTime(p.updatedAt, lang) }}</span></div>
      </FuiCard>
    </FuiAsync>

    <template v-if="runningProjects.length">
      <h3 class="home__h">{{ t('home.runningTasks') }}</h3>
      <FuiCard v-for="p in runningProjects" :key="'run-' + p.id" clickable :title="p.name" @click="router.push(`/projects/${p.id}`)">
        <FuiStatusTag tone="primary" :label="t('status.open')" />
        <div class="home__meta"><span>{{ currentStage(p) ? stageTypeLabel(currentStage(p)!.stageType) : '' }}</span></div>
      </FuiCard>
    </template>

    <template v-if="recentDeploys.length">
      <h3 class="home__h">{{ t('home.recentDeploys') }}</h3>
      <FuiCard v-for="r in recentDeploys" :key="'dep-' + r.id" clickable @click="router.push('/runs/' + r.id)">
        <template #title>{{ r.environmentKey }}</template>
        <template #extra><FuiStatusTag :tone="toneForRunStatus(r.status)" :label="stageStatusLabel(r.status)" /></template>
        <div class="home__meta"><span>{{ fmtRelTime(r.startedAt ?? r.completedAt, lang) }}</span></div>
      </FuiCard>
    </template>
  </div>
</template>

<style scoped>
.home { padding: var(--fui-sp-4); }
.home__quick { margin-bottom: var(--fui-sp-4); }
.home__quick-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--fui-sp-2); margin-top: var(--fui-sp-2); }
.home__todo-num { font-size: var(--fui-fs-lg); font-weight: 700; color: var(--fui-primary); }
.home__h { font-size: var(--fui-fs-lg); margin: var(--fui-sp-5) 0 var(--fui-sp-2); }
.home__meta { display: flex; justify-content: space-between; color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin-top: 6px; }
</style>
