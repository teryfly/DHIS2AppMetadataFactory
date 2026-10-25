<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiFilterBar } from '@/fui';
import { useWorkflowOfProject, useArtifacts } from '@/api/queries';
import { usePrefsStore } from '@/stores/prefs';
import { fmtRelTime } from '@/lib/time';
import { capabilityLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));

const projectId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflowOfProject(projectId);
const workflowId = computed(() => workflow.data.value?.id ?? '');

const typeFilter = ref('');
const artifacts = useArtifacts(workflowId, () => (typeFilter.value ? { type: typeFilter.value } : undefined));
const list = computed(() => artifacts.data.value?.items ?? []);

const options = computed(() => {
  const types = Array.from(new Set(list.value.map((a) => a.type).filter(Boolean))) as string[];
  return [{ value: '', label: t('common.all') }, ...types.map((ty) => ({ value: ty, label: ty }))];
});
</script>

<template>
  <div class="af">
    <FuiFilterBar v-model="typeFilter" :options="options" />
    <FuiAsync :loading="artifacts.isLoading.value" :empty="!list.length" :empty-title="t('review.artifacts')">
      <FuiCard v-for="a in list" :key="a.id" clickable :title="String(a.type ?? '')" @click="router.push(`/artifacts/${a.id}`)">
        <template #extra><FuiStatusTag tone="info" :label="String(a.version ?? '')" /></template>
        <div class="af__meta"><span>{{ capabilityLabel(a.creatorSubsystem) }}</span><span>{{ fmtRelTime(a.createdAt, lang) }}</span></div>
      </FuiCard>
    </FuiAsync>
  </div>
</template>

<style scoped>
.af { padding: var(--fui-sp-2) 0; }
.af__meta { display: flex; justify-content: space-between; color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin-top: 6px; }
</style>
