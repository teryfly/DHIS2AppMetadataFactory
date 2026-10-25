<script setup lang="ts">
// UM-REV-005 / UM-CAP-003 — the API review surface is READ-ONLY.
//
// It renders the OpenAPI artifact the Factory compiled. It must never gain a control that sends a
// request to a target DHIS2 instance, and it must not proxy such a request through S1 either
// (总体 SRS C-08; S4 DX-BR-01 reserves DHIS2 access for S4, behind approval and commit). Interface
// debugging happens outside the Factory, with the development package. The property is enforced, not
// hoped for: `src/test/isolation.test.ts` scans this directory for interactive-request vocabulary
// and for outbound primitives, and audits the dependency list.
//
// Mobile (UM-REV-005) keeps the per-page operation cards. Desktop shows the full document view
// (UM-CAP-003) — the same component the shareable `/projects/:id/api/doc` route renders.
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiOpenApiViewer, FuiButton } from '@/fui';
import { useWorkflowOfProject, useArtifacts, useArtifactContent } from '@/api/queries';
import { isDesktop } from '@/lib/breakpoint';
import { missingArtifactIntent, readIntentOf } from '@/api/notGenerated';
import type { ReadIntent } from '@/api/notGenerated';
import { parseApiDocument } from '@/lib/openapi/parseDoc';
import { buildDocModel } from '@/lib/openapi/docModel';
import type { ApiDocModel } from '@/lib/openapi/docModel';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflowOfProject(projectId);
const workflowId = computed(() => workflow.data.value?.id ?? '');
const artifacts = useArtifacts(workflowId);

// S1's artifact type token is exactly `OPENAPI` (S1 `factory_control/testing/fakes.py`, and the
// artifact-type enum in the contract). Matching `/openapi|api/i` against `a.type` matched the wrong
// things and, combined with `JSON.parse` on a YAML document, rendered zero operations (bug B2).
const apiArtifactId = computed(() => String((artifacts.data.value?.items ?? []).find((a) => String(a.type ?? '') === 'OPENAPI')?.id ?? ''));
const content = useArtifactContent(apiArtifactId);

const docText = computed<string | null>(() => {
  const raw = content.data.value;
  if (raw === null || raw === undefined) return null;
  return typeof raw === 'string' ? raw : JSON.stringify(raw);
});

const parsed = computed(() => parseApiDocument(docText.value));
const model = computed<ApiDocModel | null>(() => (parsed.value.ok ? buildDocModel(parsed.value) : null));

// The cards read the same model the desktop viewer reads, so the two cannot disagree.
const operations = computed(() => model.value?.operations ?? []);
const info = computed(() => (model.value ? { title: model.value.info.title, version: model.value.info.version } : null));

const methodTone = (value: string): 'success' | 'info' | 'warning' | 'danger' | 'primary' =>
  value === 'GET' ? 'success' : value === 'POST' ? 'info' : value === 'PUT' || value === 'PATCH' ? 'warning' : 'danger';

const loading = computed(() => workflow.isLoading.value || artifacts.isLoading.value || (!!apiArtifactId.value && content.isLoading.value));

// UM-REV-007: an artifact that has not been compiled yet is guidance naming the stage, not a 404.
const intent = computed<ReadIntent>(() => {
  if (artifacts.isError.value) return readIntentOf(artifacts.error.value, 'apiDoc');
  if (content.isError.value) return readIntentOf(content.error.value, 'apiDoc');
  if (!loading.value && !apiArtifactId.value) return missingArtifactIntent('apiDoc');
  return { kind: 'ok' };
});
</script>

<template>
  <div class="api">
    <FuiAsync :loading="loading" :intent="intent" :project-id="projectId" :empty-title="t('api.title')" :empty-hint="t('api.none')">
      <FuiCard v-if="info">
        <template #title>{{ info.title }}</template>
        <template #extra>
          <FuiStatusTag tone="info" :label="info.version" />
        </template>
      </FuiCard>

      <div class="api__count">
        <FuiStatusTag tone="primary" :label="`${t('api.operations')} ${operations.length}`" />
        <FuiStatusTag tone="muted" :label="`${t('api.endpoints')} ${new Set(operations.map((o) => o.path)).size}`" />
        <FuiButton v-if="isDesktop" size="mini" :label="t('api.openDoc')" @click="router.push(`/projects/${projectId}/api/doc`)" />
      </div>

      <!-- desktop: the full UM-CAP-003 document view -->
      <FuiOpenApiViewer v-if="isDesktop" :doc="docText" :loading="content.isFetching.value" :error="content.error.value" :project-id="projectId" />

      <!-- mobile: UM-REV-005 operation cards, one per interface, grouped by the page that uses it -->
      <template v-else>
        <p class="api__note">{{ t('api.desktopDoc') }}</p>
        <FuiCard v-for="o in operations" :key="o.id" :title="o.path">
          <template #extra><FuiStatusTag :tone="methodTone(o.method)" :label="o.method" /></template>
          <div class="api__sum">{{ o.summary || o.id }}</div>
          <div class="api__meta">
            <span>{{ o.id }}</span>
            <span v-if="o.parameters.length">{{ t('api.params') }} {{ o.parameters.map((p) => p.name).join(', ') }}</span>
            <span v-if="o.responses.length">{{ t('api.responses') }} {{ o.responses.map((r) => r.status).join(', ') }}</span>
            <span v-else>{{ t('api.noResponses') }}</span>
          </div>
          <div v-if="o.registryRefs.length" class="api__refs">
            <code v-for="ref in o.registryRefs" :key="ref" class="api__ref">{{ ref }}</code>
          </div>
        </FuiCard>
        <p v-if="!operations.length && !loading" class="api__note">{{ t('common.notFound') }}</p>
      </template>
    </FuiAsync>
  </div>
</template>

<style scoped>
.api { padding: var(--fui-sp-4); }
.api__count { display: flex; gap: 8px; margin-bottom: var(--fui-sp-2); align-items: center; }
.api__note { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.api__sum { font-size: var(--fui-fs-sm); margin: 4px 0; }
.api__meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.api__refs { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.api__ref { font-size: 10px; background: var(--fui-surface-2); padding: 1px 6px; border-radius: 6px; }
</style>
