<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton, FuiBottomSheet, FuiActionButton, FuiField } from '@/fui';
import { useChangeRequests, useWorkflowOfProject } from '@/api/queries';
import { useCreateChangeRequest, useChangeRequestStep } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { fmtRelTime } from '@/lib/time';
import { usePrefsStore } from '@/stores/prefs';
import { stageStatusLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));
const projectId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflowOfProject(projectId);
const workflowId = computed(() => workflow.data.value?.id ?? '');

const crs = useChangeRequests(projectId);
const create = useCreateChangeRequest();
const step = useChangeRequestStep();

const list = computed(() => crs.data.value?.items ?? []);

const sheet = ref(false);
const form = reactive({ reason: '', keys: '', retiring: '' });
const split = (value: string): string[] => value.split(',').map((k) => k.trim()).filter(Boolean);
function submitCreate(): void {
  create.mutate(
    {
      projectId: projectId.value,
      body: {
        reason: form.reason,
        affectedBusinessKeys: split(form.keys),
        // CP-CR-005: the request names its own retirements. S2's impact read has no deletion field,
        // so nothing else ever will, and S1 refuses a name that is not a DEPRECATED entry.
        deletionCandidates: split(form.retiring),
      },
    },
    { onSuccess: () => { sheet.value = false; form.reason = ''; form.keys = ''; form.retiring = ''; } },
  );
}

const impacts = reactive<Record<string, Record<string, unknown>>>({});
function showImpact(id: string): void {
  step.mutate({ crId: id, step: 'impact', projectId: projectId.value }, { onSuccess: (res) => (impacts[id] = (res as Record<string, unknown>) ?? {}) });
}
function doStep(id: string, s: 'approve' | 'reject' | 'start' | 'complete' | 'cancel'): void {
  step.mutate({ crId: id, step: s, projectId: projectId.value, body: s === 'reject' ? { reason: t('changeRequests.rejectReasonDefault') } : undefined });
}
</script>

<template>
  <div class="cr">
    <div class="cr__bar"><FuiButton variant="primary" round :label="t('changeRequests.create')" @click="sheet = true" /></div>
    <FuiAsync :loading="crs.isLoading.value" :empty="!list.length" :empty-title="t('changeRequests.title')">
      <FuiCard v-for="c in list" :key="c.id">
        <template #title>{{ c.reason }}</template>
        <template #extra><FuiStatusTag tone="info" :label="stageStatusLabel(String(c.status ?? ''))" /></template>
        <div class="cr__meta">{{ fmtRelTime(c.createdAt, lang) }}</div>
        <div class="cr__steps">
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.changeRequestImpact" :target-id="c.id" size="mini" :label="t('changeRequests.impact')" @click="showImpact(c.id)" />
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.changeRequestStart" :target-id="c.id" size="mini" :label="t('changeRequests.start')" @click="doStep(c.id, 'start')" />
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.changeRequestApprove" :target-id="c.id" size="mini" variant="primary" :label="t('changeRequests.approve')" @click="doStep(c.id, 'approve')" />
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.changeRequestReject" :target-id="c.id" size="mini" variant="danger" :label="t('changeRequests.reject')" @click="doStep(c.id, 'reject')" />
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.changeRequestComplete" :target-id="c.id" size="mini" :label="t('changeRequests.complete')" @click="doStep(c.id, 'complete')" />
          <FuiActionButton :workflow-id="workflowId" :action="ACTION.changeRequestCancel" :target-id="c.id" size="mini" :label="t('changeRequests.cancel')" @click="doStep(c.id, 'cancel')" />
        </div>
        <pre v-if="impacts[c.id]" class="cr__impact" role="region" tabindex="0" :aria-label="t('changeRequests.impact')">{{ JSON.stringify(impacts[c.id], null, 2) }}</pre>
      </FuiCard>
    </FuiAsync>

    <FuiBottomSheet v-model="sheet" :title="t('changeRequests.create')">
      <FuiField v-model="form.reason" type="textarea" rows="2" :label="t('changeRequests.reason')" />
      <FuiField v-model="form.keys" :label="t('changeRequests.affectedKeys')" :placeholder="t('changeRequests.searchRegistry')" />
      <FuiField
        v-model="form.retiring"
        :label="t('changeRequests.retireKeys')"
        :help="t('changeRequests.retireKeysHint')"
      />
      <template #footer><FuiButton variant="primary" block :label="t('common.create')" :loading="create.isPending.value" @click="submitCreate" /></template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.cr { padding: var(--fui-sp-4); }
.cr__bar { display: flex; justify-content: flex-end; margin-bottom: var(--fui-sp-2); }
.cr__meta { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 4px 0 8px; }
.cr__steps { display: flex; flex-wrap: wrap; gap: 6px; }
.cr__impact { white-space: pre-wrap; font-size: var(--fui-fs-xs); background: var(--fui-surface-2); padding: 8px; border-radius: 8px; overflow: auto; }
</style>
