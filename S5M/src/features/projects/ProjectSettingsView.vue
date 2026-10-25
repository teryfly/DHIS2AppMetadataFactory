<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton, FuiActionButton, FuiBottomSheet, FuiField } from '@/fui';
import { useProject, useUidPool, useProjectSettings } from '@/api/queries';
import { useUpdateTargetEnvironments, useSaveProjectSettings, useArchiveProject, useExportProject, useDeleteProject, useUpgradeMode, useImportProject } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { envClassLabel } from '@/lib/labels';
import { pushSuccess } from '@/lib/toast';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));
const project = useProject(projectId);
const proj = computed(() => project.data.value);
const uidPool = useUidPool(projectId);
const upgrade = useUpgradeMode();
function doUpgrade(): void {
  upgrade.mutate({ projectId: projectId.value, workflowId: proj.value?.workflowId ?? undefined }, { onSuccess: () => pushSuccess(t('project.upgradeDone')) });
}
const updateEnvs = useUpdateTargetEnvironments();
const saveSettings = useSaveProjectSettings();
const archive = useArchiveProject();
const exportProj = useExportProject();
const del = useDeleteProject();

// UI-PRJ-004 pairs 导出 with 导入; the mobile screen had the first and not the second, so an
// operator who exported on the phone could only ever restore at a desk. The import creates a
// project, so its right is the platform answer (`project.import`), not this workflow's.
const importProj = useImportProject();
const importSheet = ref(false);
const importArtifact = ref('');
function doImport(): void {
  importProj.mutate(
    { artifactId: importArtifact.value.trim() },
    {
      onSuccess: (res) => {
        importSheet.value = false;
        const restored = String((res as { stepRunId?: string } | undefined)?.stepRunId ?? '');
        pushSuccess(t('project.imported', { id: restored || '—' }));
        importArtifact.value = '';
      },
    },
  );
}

const targetEnvs = computed(() => project.data.value?.targetEnvironments ?? []);
function setDesignRef(key: string): void {
  const body = targetEnvs.value.map((e) => ({ environmentKey: e.environmentKey, isDesignReference: e.environmentKey === key, promotionOrder: e.promotionOrder }));
  updateEnvs.mutate({ projectId: projectId.value, body });
}
function move(i: number, dir: -1 | 1): void {
  const arr = [...targetEnvs.value];
  const j = i + dir;
  if (j < 0 || j >= arr.length) return;
  [arr[i], arr[j]] = [arr[j], arr[i]];
  updateEnvs.mutate({ projectId: projectId.value, body: arr.map((e, idx) => ({ environmentKey: e.environmentKey, isDesignReference: e.isDesignReference, promotionOrder: (idx + 1) * 100 })) });
}

// Read back before it is edited: these three fields used to open on invented defaults, so pressing
// Save wrote 24 / nobody / nothing over whatever the project actually had (the endpoint existed and
// its query key was declared — nothing ever issued the GET).
const settingsForm = reactive({ gateReminderHours: 0, notificationEmails: '', notificationWebhookRef: '' });
const settingsLoaded = ref(false);
const projectSettingsQ = useProjectSettings(projectId);
watch(
  () => projectSettingsQ.data.value as Record<string, unknown> | undefined,
  (d) => {
    if (!d) return;
    settingsForm.gateReminderHours = Number(d.gateReminderHours ?? 0);
    settingsForm.notificationEmails = ((d.notificationEmails as string[] | undefined) ?? []).join(', ');
    settingsForm.notificationWebhookRef = String(d.notificationWebhookRef ?? '');
    settingsLoaded.value = true;
  },
  { immediate: true },
);
function saveProjectSettings(): void {
  saveSettings.mutate({ projectId: projectId.value, body: { gateReminderHours: settingsForm.gateReminderHours, notificationEmails: settingsForm.notificationEmails.split(',').map((s) => s.trim()).filter(Boolean), notificationWebhookRef: settingsForm.notificationWebhookRef || undefined } }, { onSuccess: () => pushSuccess(t('settings.saved')) });
}

const deleteSheet = ref(false);
const deleteName = ref('');
function doDelete(): void {
  if (deleteName.value !== project.data.value?.name) return;
  del.mutate(projectId.value, { onSuccess: () => router.replace('/projects') });
}
</script>

<template>
  <div class="ps">
    <FuiAsync :loading="project.isLoading.value" @retry="project.refetch()">
      <FuiCard :title="t('project.targetEnvs')">
        <div v-for="(e, i) in targetEnvs" :key="e.environmentKey" class="ps__env">
          <div class="ps__envmain">
            <b>{{ e.environmentKey }}</b>
            <FuiStatusTag tone="info" :label="envClassLabel(e.environmentClass)" />
            <FuiStatusTag v-if="e.isDesignReference" tone="primary" :label="t('deployments.designRef')" />
          </div>
          <div class="ps__envctrl">
            <FuiButton size="mini" @click="move(i, -1)">↑</FuiButton>
            <FuiButton size="mini" @click="move(i, 1)">↓</FuiButton>
            <FuiButton v-if="!e.isDesignReference" size="mini" :label="t('project.changeDesignRef')" @click="setDesignRef(e.environmentKey)" />
          </div>
        </div>
        <p class="ps__hint">{{ t('project.designRefImpact') }}</p>
      </FuiCard>

      <FuiCard :title="t('project.settings')">
        <FuiField v-model="settingsForm.gateReminderHours" type="number" :label="t('project.remindHours')" />
        <FuiField v-model="settingsForm.notificationEmails" :label="t('settings.email')" />
        <FuiField v-model="settingsForm.notificationWebhookRef" :label="t('settings.webhookSecret')" />
        <FuiButton :label="t('settings.save')" :loading="saveSettings.isPending.value" @click="saveProjectSettings" />
      </FuiCard>

      <FuiCard :title="t('project.upgradeMode')">
        <div v-if="uidPool.data.value" class="ps__pool">
          <span>{{ t('project.uidAvailable', { n: uidPool.data.value.available }) }}</span>
          <FuiStatusTag v-if="uidPool.data.value.needsReplenishment" tone="warning" :label="t('project.uidReplenish')" />
        </div>
        <FuiActionButton :action="ACTION.projectUpgradeMode" :workflow-id="proj?.workflowId ?? ''" :target-id="projectId" size="small" :label="t('project.upgradeMode')" @click="doUpgrade" />
      </FuiCard>

      <FuiCard :title="t('common.actions')">
        <div class="ps__acts">
          <FuiActionButton :action="ACTION.projectExport" :workflow-id="proj?.workflowId ?? ''" :target-id="projectId" size="small" :label="t('project.export')" @click="exportProj.mutate(projectId)" />
          <FuiActionButton :action="ACTION.projectImport" platform size="small" :label="t('project.importProjects')" @click="importSheet = true" />
          <FuiActionButton :action="ACTION.projectArchive" :workflow-id="proj?.workflowId ?? ''" :target-id="projectId" size="small" :label="proj?.archived ? t('project.unarchive') : t('project.archive')" @click="archive.mutate(projectId)" />
          <FuiActionButton :action="ACTION.projectDelete" :workflow-id="proj?.workflowId ?? ''" :target-id="projectId" size="small" variant="danger" :label="t('project.deleteProject')" @click="deleteSheet = true" />
        </div>
      </FuiCard>
    </FuiAsync>

    <FuiBottomSheet v-model="deleteSheet" :title="t('project.deleteProject')">
      <p class="ps__warn">{{ t('project.deleteConfirmType') }}</p>
      <FuiField v-model="deleteName" :placeholder="proj?.name" />
      <template #footer><FuiButton variant="danger" block :label="t('common.confirmDelete')" :disabled="deleteName !== proj?.name" :loading="del.isPending.value" @click="doDelete" /></template>
    </FuiBottomSheet>
    <FuiBottomSheet v-model="importSheet" :title="t('project.importProjects')">
      <p class="ps__hint">{{ t('project.importHelp') }}</p>
      <FuiField v-model="importArtifact" :label="t('project.importArtifact')" placeholder="art-…" />
      <template #footer><FuiButton variant="primary" block :label="t('project.importProjects')" :disabled="!importArtifact.trim()" :loading="importProj.isPending.value" @click="doImport" /></template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.ps { padding: var(--fui-sp-4); }
.ps__env { padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.ps__envmain { display: flex; gap: 8px; align-items: center; }
.ps__envctrl { display: flex; gap: 6px; margin-top: 6px; }
.ps__hint { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.ps__acts { display: flex; flex-wrap: wrap; gap: 8px; }
.ps__warn { font-size: var(--fui-fs-sm); color: var(--fui-danger); }
.ps__pool { display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-sm); margin-bottom: 8px; }
</style>
