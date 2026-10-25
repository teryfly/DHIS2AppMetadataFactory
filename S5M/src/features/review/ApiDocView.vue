<script setup lang="ts">
// UM-REV-005 / UM-CAP-003 — read-only OpenAPI document route.
//
// `/projects/:id/api/doc` gives the desktop document view a URL that can be pasted into a review
// note, which the tabbed `/projects/:id/api` page cannot. Same artifact read, same viewer, same
// guarantee: nothing here may gain a control that sends a request to a target DHIS2 instance, and
// nothing does — `src/test/isolation.test.ts` is the enforcement of record.
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiAsync, FuiOpenApiViewer, FuiNotice } from '@/fui';
import { useWorkflowOfProject, useArtifacts, useArtifactContent } from '@/api/queries';
import { isDesktop } from '@/lib/breakpoint';
import { missingArtifactIntent, readIntentOf } from '@/api/notGenerated';
import type { ReadIntent } from '@/api/notGenerated';

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflowOfProject(projectId);
const workflowId = computed(() => workflow.data.value?.id ?? '');
const artifacts = useArtifacts(workflowId);

const apiArtifactId = computed(() => String((artifacts.data.value?.items ?? []).find((a) => String(a.type ?? '') === 'OPENAPI')?.id ?? ''));
const content = useArtifactContent(apiArtifactId);

const docText = computed<string | null>(() => {
  const raw = content.data.value;
  if (raw === null || raw === undefined) return null;
  return typeof raw === 'string' ? raw : JSON.stringify(raw);
});

const loading = computed(() => workflow.isLoading.value || artifacts.isLoading.value || (!!apiArtifactId.value && content.isLoading.value));

const intent = computed<ReadIntent>(() => {
  if (artifacts.isError.value) return readIntentOf(artifacts.error.value, 'apiDoc');
  if (content.isError.value) return readIntentOf(content.error.value, 'apiDoc');
  if (!loading.value && !apiArtifactId.value) return missingArtifactIntent('apiDoc');
  return { kind: 'ok' };
});
</script>

<template>
  <div class="apidoc">
    <FuiAsync :loading="loading" :intent="intent" :project-id="projectId" :empty-title="t('api.title')" :empty-hint="t('api.none')">
      <!-- Below the desktop breakpoint the viewer renders nothing by design (UM-REV-005), so the
           route says where the reader is and hands them back to the card list. -->
      <FuiNotice v-if="!isDesktop" tone="info" :text="t('api.desktopDoc')" />
      <FuiOpenApiViewer :doc="docText" :loading="loading" :error="content.error.value" :project-id="projectId" />
    </FuiAsync>
  </div>
</template>

<style scoped>
.apidoc { padding: var(--fui-sp-4); }
</style>
