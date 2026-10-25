<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton, FuiNotice } from '@/fui';
import { useFrontendPackage, useFrontendPackageFile, useAllowedActions, useWorkflowOfProject } from '@/api/queries';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { readIntentOf, type ReadIntent } from '@/api/notGenerated';
import { apiPrefix } from '@/config/runtime';
import { handoffKindLabel } from '@/lib/labels';

// CP-UI-021 (UM-REV-007): the mode-B development package — what a frontend team is handed. S1
// forwards S2's own listing and never unpacks it, so the layout here is S2's and `kind` is S2's
// word, not the build package's `role`.
//
// The integrity check is deliberately not restated: it is a result of the HANDOFF stage, and the
// stage's own screen carries the verdict. Repeating it here would be a copy of a verdict this page
// cannot see.

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));

// UM-BR-01: `frontend_package.download` is what says whether there is a package to read, so the
// page asks it before reading, and does not fire a request S1 has already answered "no".
const workflow = useWorkflowOfProject(projectId);
const actions = useAllowedActions(computed(() => workflow.data.value?.id ?? ''));
const gate = computed(() => resolveAvailability(indexActions(actions.data.value), ACTION.frontendPackageDownload));
const waiting = computed(() => workflow.isLoading.value || actions.isLoading.value);

const pkg = useFrontendPackage(projectId, computed(() => gate.value.available));
const files = computed(() => pkg.data.value?.files ?? []);
const openPath = ref('');
const file = useFrontendPackageFile(projectId, () => openPath.value);

const isEmpty = computed(() => !files.value.length && !pkg.isLoading.value);
// Withheld for the expected reason, S1 says the package has not been produced; withheld any other
// way (an archived project blocks the whole list) does not promise a stage that will never run.
const otherReason = computed(() => !gate.value.available && !!gate.value.reasonCode && gate.value.reasonCode !== 'PACKAGE_NOT_GENERATED');
const withheldReason = computed(() => (otherReason.value ? t(`actionReason.${gate.value.reasonCode}`) : ''));
const intent = computed<ReadIntent>(() => {
  if (!waiting.value && !gate.value.available) {
    return otherReason.value
      ? { kind: 'notExist', surface: 'package' }
      : { kind: 'notGenerated', requiredStage: 'HANDOFF', surface: 'package' };
  }
  return readIntentOf(pkg.error.value as never, 'package');
});

function download(path: string): string {
  return `${apiPrefix()}/projects/${encodeURIComponent(projectId.value)}/frontend-package/files/${path.split('/').map(encodeURIComponent).join('/')}?download=true`;
}
</script>

<template>
  <div class="hf">
    <FuiAsync
      :loading="waiting || pkg.isLoading.value"
      :error="pkg.isError.value && intent.kind === 'error'"
      :empty="isEmpty"
      :intent="intent"
      :project-id="projectId"
      :empty-title="t('handoff.title')"
      :empty-hint="t('handoff.empty')"
      @retry="pkg.refetch()"
    >
      <template #fallback>
        <FuiNotice v-if="withheldReason" tone="warning" :text="withheldReason" />
      </template>
      <FuiCard :title="t('handoff.files')">
        <p class="hf__meta">{{ t('handoff.blueprintVersion') }} · {{ pkg.data.value?.blueprintVersion || '—' }}</p>
        <p class="hf__hint">{{ t('handoff.integrityHint') }}</p>
        <div v-for="f in files" :key="f.path" class="hf__row">
          <div class="hf__body">
            <div class="hf__path">{{ f.path }}</div>
            <div class="hf__meta">
              <FuiStatusTag tone="info" :label="handoffKindLabel(f.kind ?? 'DOCUMENT')" />
              <span>{{ f.sizeBytes ?? 0 }} B</span>
              <code class="hf__hash">{{ String(f.contentHash ?? '').slice(0, 10) }}</code>
            </div>
          </div>
          <div class="hf__acts">
            <FuiButton size="mini" :label="t('handoff.view')" @click="openPath = f.path ?? ''" />
            <a class="hf__dl" :href="download(f.path ?? '')">{{ t('handoff.download') }}</a>
          </div>
        </div>
      </FuiCard>

      <FuiCard v-if="openPath" :title="openPath">
        <template #extra><FuiButton size="mini" :label="t('handoff.close')" @click="openPath = ''" /></template>
        <p v-if="file.isLoading.value" class="hf__hint">{{ t('common.loading') }}</p>
        <FuiNotice v-else-if="file.isError.value" tone="danger" :text="t('handoff.notExist')" />
        <pre v-else class="hf__code" role="region" tabindex="0" :aria-label="openPath">{{ file.data.value ?? '' }}</pre>
      </FuiCard>
    </FuiAsync>
  </div>
</template>

<style scoped>
.hf { padding: var(--fui-sp-4); }
.hf__hint { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 4px 0; }
.hf__row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.hf__body { min-width: 0; flex: 1; }
.hf__path { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); word-break: break-all; }
.hf__meta { display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin-top: 4px; }
.hf__hash { font-size: 10px; color: var(--fui-text-tertiary); }
.hf__acts { display: flex; gap: 8px; align-items: center; flex: none; }
.hf__dl { font-size: var(--fui-fs-xs); color: var(--fui-primary); min-height: var(--fui-tap-min); min-width: var(--fui-tap-min); justify-content: center; display: inline-flex; align-items: center; }
.hf__code { margin: 0; max-height: 320px; overflow: auto; font-size: var(--fui-fs-xs); background: var(--fui-surface-2); padding: 8px; border-radius: var(--fui-radius-btn); }
</style>
