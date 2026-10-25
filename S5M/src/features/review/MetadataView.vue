<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiDiffViewer } from '@/fui';
import { useRegistry, useBlueprintVersions, useBlueprintDiff } from '@/api/queries';
import { readIntentOf, type ReadIntent } from '@/api/notGenerated';
import type { Tone } from '@/lib/labels';
import type { DiffObject } from '@/api/types';
import { dispositionLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));
const registry = useRegistry(projectId);
const entries = computed(() => (registry.data.value?.items ?? []) as Record<string, unknown>[]);
// UM-REV-007: the registry is derived from a compiled blueprint, so a 404 here means "not yet".
const intent = computed<ReadIntent>(() => (registry.isError.value ? readIntentOf(registry.error.value, 'registry') : { kind: 'ok' }));

const versions = useBlueprintVersions(projectId);
const sortedVersions = computed<Record<string, unknown>[]>(() =>
  [...((versions.data.value?.items ?? []) as unknown as Record<string, unknown>[])]
    .sort((a, b) => String(b.version ?? '').localeCompare(String(a.version ?? ''), undefined, { numeric: true })),
);
const toV = computed(() => String(sortedVersions.value[0]?.version ?? ''));
const fromV = computed(() => String(sortedVersions.value[0]?.parentVersion ?? sortedVersions.value[1]?.version ?? ''));
const diff = useBlueprintDiff(projectId, fromV, toV);
const diffObjects = computed<DiffObject[]>(() => (diff.data.value?.objects ?? []) as unknown as DiffObject[]);

function dispTone(d?: string): Tone {
  switch (d) {
    case 'CREATE':
      return 'success';
    case 'UPDATE':
      return 'info';
    case 'DEACTIVATE':
    case 'DELETE':
      return 'danger';
    case 'EXTERNAL':
    case 'REFERENCE':
      return 'muted';
    default:
      return 'primary';
  }
}
const stats = computed(() => {
  const c: Record<string, number> = {};
  for (const e of entries.value) c[String(e.disposition ?? 'UNKNOWN')] = (c[String(e.disposition ?? 'UNKNOWN')] ?? 0) + 1;
  return c;
});
const grouped = computed(() => {
  const g: Record<string, Record<string, unknown>[]> = {};
  for (const e of entries.value) (g[String(e.dhis2Type ?? '—')] ??= []).push(e);
  return g;
});
</script>

<template>
  <div class="md">
    <FuiAsync :loading="registry.isLoading.value" :error="registry.isError.value && intent.kind === 'error'" :empty="!entries.length" :intent="intent" :project-id="projectId" :empty-title="t('review.metadata')" @retry="registry.refetch()">
      <FuiCard :title="t('review.stats')">
        <div class="md__stats">
          <span v-for="(n, d) in stats" :key="d"><FuiStatusTag :tone="dispTone(String(d))" :label="`${dispositionLabel(String(d))} ${n}`" /></span>
        </div>
      </FuiCard>

      <FuiCard v-if="diffObjects.length" :title="`${t('review.changedFields')} (${fromV} → ${toV})`">
        <div v-for="o in diffObjects" :key="o.businessKey" class="md__obj">
          <div class="md__objhead">
            <FuiStatusTag :tone="dispTone(o.disposition as string)" :label="dispositionLabel(o.disposition as string)" />
            <b>{{ o.dhis2Type }}</b>
            <span class="md__key">{{ o.businessKey }}</span>
          </div>
          <FuiDiffViewer :lines="(o.changes ?? []) as unknown as Record<string, unknown>[]" />
        </div>
      </FuiCard>

      <FuiCard v-for="(items, type) in grouped" :key="type" :title="String(type)">
        <div v-for="e in items" :key="String(e.businessKey)" class="md__obj">
          <FuiStatusTag :tone="dispTone(e.disposition as string)" :label="dispositionLabel(e.disposition as string)" />
          <span class="md__key">{{ e.businessKey }}</span>
          <span class="md__code">{{ e.code ?? e.uid ?? '' }}</span>
        </div>
      </FuiCard>
      <p class="md__note">{{ t('review.graphSoon') }}</p>
    </FuiAsync>
  </div>
</template>

<style scoped>
.md { padding: var(--fui-sp-4); }
.md__stats { display: flex; flex-wrap: wrap; gap: 8px; }
.md__obj { padding: var(--fui-sp-2) 0; border-bottom: 1px solid var(--fui-border); }
.md__objhead { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.md__key { font-size: var(--fui-fs-sm); flex: 1; }
.md__code { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.md__note { text-align: center; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
