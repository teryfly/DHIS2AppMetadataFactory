<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiSegmented, FuiActionButton } from '@/fui';
import { useProjects } from '@/api/queries';
import { ACTION } from '@/api/actions';
import { usePrefsStore } from '@/stores/prefs';
import { fmtRelTime } from '@/lib/time';
import { stageTypeLabel, stageStatusLabel, toneForStageStatus } from '@/lib/labels';
import type { ProjectView, ProjectStageView } from '@/api/types';

const { t } = useI18n();
const router = useRouter();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));

const showArchived = ref(false);
const archiveOptions = computed(() => [
  { value: 'active', label: t('projects.activeOnly') },
  { value: 'all', label: t('projects.includeArchived') },
]);
const projects = useProjects(() => showArchived.value);
const items = computed<ProjectView[]>(() => projects.data.value?.items ?? []);
const currentStage = (p: ProjectView): ProjectStageView | undefined => p.stages?.find((s) => s.id === p.currentStageId);
</script>

<template>
  <div class="pl">
    <div class="pl__bar">
      <FuiActionButton variant="primary" round platform :action="ACTION.projectCreate" :label="t('projects.newProject')" @click="router.push('/projects/new')" />
      <!-- One control that names both states. A switch beside a caption repeating it was two labels
           for one choice, and the switch itself was the only thing you could hit. -->
      <FuiSegmented
        small
        :model-value="showArchived ? 'all' : 'active'"
        :options="archiveOptions"
        :label="t('projects.title')"
        @update:model-value="(v: string) => (showArchived = v === 'all')"
      />
    </div>

    <FuiAsync :loading="projects.isLoading.value" :empty="!items.length" :empty-title="t('projects.title')" :empty-hint="t('home.noProjects')" :empty-action="t('home.newProject')" @action="router.push('/projects/new')">
      <FuiCard v-for="p in items" :key="p.id" clickable :title="p.name" @click="router.push(`/projects/${p.id}`)">
        <template #extra>
          <div class="pl__extra">
            <FuiStatusTag v-if="p.archived" tone="muted" :label="t('projects.archived')" />
            <FuiStatusTag v-else-if="currentStage(p)" :tone="toneForStageStatus(currentStage(p)!.status)" :label="stageStatusLabel(currentStage(p)!.status)" />
          </div>
        </template>
        <div class="pl__row"><span class="pl__k">{{ t('projects.currentStage') }}</span>{{ currentStage(p) ? stageTypeLabel(currentStage(p)!.stageType) : '—' }}</div>
        <div class="pl__meta"><span>{{ t('projects.owner') }}: {{ p.ownerName }}</span><span>{{ fmtRelTime(p.updatedAt, lang) }}</span></div>
      </FuiCard>
    </FuiAsync>
  </div>
</template>

<style scoped>
.pl { padding: var(--fui-sp-4); }
.pl__bar { display: flex; align-items: center; flex-wrap: wrap; gap: var(--fui-sp-2); margin-bottom: var(--fui-sp-3); }
.pl__extra { display: flex; gap: 6px; }
.pl__row { font-size: var(--fui-fs-sm); }
.pl__k { color: var(--fui-text-tertiary); margin-right: 8px; }
.pl__meta { display: flex; justify-content: space-between; color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin-top: 6px; }
</style>
