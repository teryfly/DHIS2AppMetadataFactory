<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton, FuiActionButton, FuiBottomSheet, FuiField, FuiSegmented, FuiCheckChips } from '@/fui';
import { useProject, useUidPool, useProjectSettings } from '@/api/queries';
import { useUpdateTargetEnvironments, useSaveProjectSettings, useArchiveProject, useExportProject, useDeleteProject, useUpgradeMode, useImportProject, useUpdateProject } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { envClassLabel } from '@/lib/labels';
import { pushError, pushSuccess } from '@/lib/toast';

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
const updateProject = useUpdateProject();

// The management attributes S1's PATCH accepts. Delivery mode and target environments have
// their own endpoints; everything else about the project is edited here.
const basic = reactive({
  name: '',
  ownerName: '',
  description: '',
  projectType: 'GREENFIELD',
  versionConstraints: '',
  defaultLanguage: 'zh-CN',
  translationLanguages: [] as string[],
});
const basicLoaded = ref(false);

function normalizedType(value?: string | null): string {
  return String(value ?? '').toUpperCase() === 'BROWNFIELD' ? 'BROWNFIELD' : 'GREENFIELD';
}
function normalizedLanguage(value?: string | null): string {
  return String(value ?? '').startsWith('zh') ? 'zh-CN' : 'en';
}

const typeOptions = computed(() => [
  { value: 'GREENFIELD', label: t('projects.greenfield') },
  { value: 'BROWNFIELD', label: t('projects.brownfield') },
]);
const languageOptions = computed(() => [
  { value: 'zh-CN', label: t('me.langZh') },
  { value: 'en', label: t('me.langEn') },
]);
// The default language is not a *translation* of itself; offering it here would let the operator
// create a state the save path silently drops.
const translationOptions = computed(() => languageOptions.value.filter((option) => option.value !== basic.defaultLanguage));

const basicDirty = computed(() => {
  const p = proj.value;
  if (!p) return false;
  const defaultLanguage = normalizedLanguage(p.defaultLanguage);
  return (
    basic.name !== (p.name ?? '') ||
    basic.ownerName !== (p.ownerName ?? '') ||
    basic.description !== (p.description ?? '') ||
    basic.projectType !== normalizedType(p.projectType) ||
    basic.versionConstraints !== (p.versionConstraints ?? '') ||
    basic.defaultLanguage !== defaultLanguage ||
    JSON.stringify(basic.translationLanguages) !==
      JSON.stringify((p.translationLanguages ?? []).filter((code) => code !== defaultLanguage))
  );
});
const basicValid = computed(() => !!basic.name.trim() && !!basic.ownerName.trim());

watch(
  () => proj.value,
  (p) => {
    if (!p) return;
    // A background refetch must not overwrite words the operator is still editing.
    if (basicLoaded.value && basicDirty.value) return;
    const defaultLanguage = normalizedLanguage(p.defaultLanguage);
    basic.name = p.name ?? '';
    basic.ownerName = p.ownerName ?? '';
    basic.description = p.description ?? '';
    basic.projectType = normalizedType(p.projectType);
    basic.versionConstraints = p.versionConstraints ?? '';
    basic.defaultLanguage = defaultLanguage;
    basic.translationLanguages = (p.translationLanguages ?? []).filter((code) => code !== defaultLanguage);
    basicLoaded.value = true;
  },
  { immediate: true },
);

function saveBasics(): void {
  if (!basicValid.value) return;
  const translationLanguages = basic.translationLanguages.filter((code) => code !== basic.defaultLanguage);
  updateProject.mutate(
    {
      projectId: projectId.value,
      body: {
        name: basic.name.trim(),
        ownerName: basic.ownerName.trim(),
        description: basic.description,
        projectType: basic.projectType,
        versionConstraints: basic.versionConstraints,
        defaultLanguage: basic.defaultLanguage,
        translationLanguages,
      },
    },
    {
      onSuccess: () => pushSuccess(t('project.basicSaved')),
      onError: (err) => pushError(err),
    },
  );
}

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

// Opening the irreversible sheet from `?action=delete` meant a bookmarked or reloaded URL re-popped
// it; the sheet is opened by the button below and by nothing else.
const deleteSheet = ref(false);
const deleteName = ref('');
function doDelete(): void {
  if (deleteName.value !== project.data.value?.name) return;
  del.mutate(projectId.value, {
    onSuccess: () => router.replace('/projects'),
    onError: (err) => pushError(err),
  });
}
</script>

<template>
  <div class="ps">
    <FuiAsync :loading="project.isLoading.value" @retry="project.refetch()">
      <FuiCard id="basics" :title="t('project.basicInfo')">
        <p class="ps__hint">{{ t('project.basicInfoHint') }}</p>
        <p v-if="proj?.archived" class="ps__warn">{{ t('project.archivedReadonly') }}</p>
        <FuiField v-model="basic.name" :label="t('project.name')" required :disabled="!!proj?.archived" />
        <FuiField v-model="basic.ownerName" :label="t('project.ownerName')" required :disabled="!!proj?.archived" />
        <FuiField v-model="basic.description" type="textarea" rows="2" :label="t('project.description')" :disabled="!!proj?.archived" />
        <div class="ps__choice">
          <span class="ps__choice-label">{{ t('project.projectType') }}</span>
          <FuiSegmented v-model="basic.projectType" :options="typeOptions" :disabled="!!proj?.archived" small />
        </div>
        <FuiField v-model="basic.versionConstraints" :label="t('project.versionConstraints')" :disabled="!!proj?.archived" />
        <div class="ps__choice">
          <span class="ps__choice-label">{{ t('project.defaultLanguage') }}</span>
          <FuiSegmented v-model="basic.defaultLanguage" :options="languageOptions" :disabled="!!proj?.archived" small />
        </div>
        <div class="ps__choice">
          <span class="ps__choice-label">{{ t('project.translationLanguages') }}</span>
          <FuiCheckChips v-model="basic.translationLanguages" :options="translationOptions" :disabled="!!proj?.archived" small />
        </div>
        <FuiActionButton
          :action="ACTION.projectEdit"
          :workflow-id="proj?.workflowId ?? ''"
          :target-id="projectId"
          size="small"
          :label="t('project.saveBasics')"
          :loading="updateProject.isPending.value"
          :disabled="!basicDirty || !basicValid || !!proj?.archived"
          @click="saveBasics"
        />
      </FuiCard>

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
          <FuiActionButton v-if="!proj?.archived" :action="ACTION.projectArchive" :workflow-id="proj?.workflowId ?? ''" :target-id="projectId" size="small" :label="t('project.archive')" @click="archive.mutate(projectId)" />
          <FuiActionButton v-if="proj?.archived" :action="ACTION.projectDelete" :workflow-id="proj?.workflowId ?? ''" :target-id="projectId" size="small" variant="danger" :label="t('project.deleteProject')" @click="deleteSheet = true" />
        </div>
        <p class="ps__hint">{{ t('project.deleteNeedsArchive') }}</p>
      </FuiCard>
    </FuiAsync>

    <FuiBottomSheet v-model="deleteSheet" :title="t('project.deleteProject')">
      <p class="ps__warn">{{ t('project.deleteCascade') }}</p>
      <p class="ps__hint">{{ t('project.deleteConfirmType') }}</p>
      <FuiField v-model="deleteName" :placeholder="proj?.name" />
      <template #footer>
        <FuiActionButton
          :action="ACTION.projectDelete"
          :workflow-id="proj?.workflowId ?? ''"
          :target-id="projectId"
          variant="danger"
          block
          :label="t('common.confirmDelete')"
          :disabled="deleteName !== proj?.name"
          :loading="del.isPending.value"
          @click="doDelete"
        />
      </template>
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
.ps__choice { margin: 6px 0 12px; }
.ps__choice-label { display: block; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin-bottom: 6px; }
.ps__pool { display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-sm); margin-bottom: 8px; }
</style>
