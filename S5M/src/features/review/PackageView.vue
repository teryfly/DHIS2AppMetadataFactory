<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton } from '@/fui';
import { usePackageFiles } from '@/api/queries';
import { packageIntentOf, readIntentOf, type ReadIntent } from '@/api/notGenerated';
import { copyText, currentUrl } from '@/lib/clipboard';
import { pushSuccess } from '@/lib/toast';
import { artifactKindLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));
const pkg = usePackageFiles(projectId);
const pk = computed(() => pkg.data.value);

const files = computed(() => (pkg.data.value?.files ?? []) as { path: string; role?: string; dhis2Type?: string | null; objectCount?: number | null; contentHash?: string | null }[]);
const isEmpty = computed(() => (pkg.data.value?.kind ?? 'NONE') === 'NONE' && !files.value.length);

// UM-REV-007: "no package yet" is guidance, not an empty panel. The failure path and the
// kind=NONE success path both funnel into the same intent so the copy cannot diverge.
const intent = computed<ReadIntent>(() =>
  pkg.isError.value
    ? readIntentOf(pkg.error.value, 'package')
    : packageIntentOf(pkg.data.value as { kind?: string; files?: unknown[] } | undefined),
);

function copyDownload(): void {
  void copyText(currentUrl()).then(() => pushSuccess(t('common.copied')));
}
</script>

<template>
  <div class="pkg">
    <FuiAsync :loading="pkg.isLoading.value" :error="pkg.isError.value && intent.kind === 'error'" :empty="isEmpty" :intent="intent" :project-id="projectId" :empty-title="t('pkg.title')" :empty-hint="t('pkg.empty')" @retry="pkg.refetch()">
      <FuiCard>
        <template #title>{{ t('pkg.title') }}</template>
        <template #extra><FuiStatusTag :tone="pk?.kind && pk.kind !== 'NONE' ? 'success' : 'muted'" :label="pk?.kind && pk.kind !== 'NONE' ? artifactKindLabel(pk.kind) : t('pkg.none')" /></template>
        <div class="pkg__row"><span>{{ t('review.versions') }}</span>{{ pk?.version }}</div>
        <div class="pkg__row"><span>{{ t('pkg.buildId') }}</span>{{ pk?.buildId || '—' }}</div>
        <div class="pkg__row"><span>{{ t('pkg.packageHash') }}</span><code>{{ String(pk?.packageHash ?? '—').slice(0, 24) }}</code></div>
        <div class="pkg__row"><span>{{ t('deployments.title') }}</span>{{ pk?.environmentKey || '—' }}</div>
        <FuiButton v-if="!isEmpty" size="small" :label="t('pkg.download')" @click="copyDownload" />
      </FuiCard>

      <FuiCard :title="`${t('pkg.files')} (${files.length})`">
        <div v-for="f in files" :key="f.path" class="pkg__file">
          <div class="pkg__fpath">{{ f.path }}</div>
          <div class="pkg__fmeta">
            <FuiStatusTag v-if="f.role" tone="info" :label="f.role" />
            <span v-if="f.dhis2Type">{{ f.dhis2Type }}</span>
            <span v-if="f.objectCount != null">{{ t('pkg.objects') }} {{ f.objectCount }}</span>
            <code v-if="f.contentHash" class="pkg__hash">{{ f.contentHash.toString().slice(0, 10) }}</code>
          </div>
        </div>
        <p v-if="!files.length" class="pkg__muted">{{ t('pkg.empty') }}</p>
      </FuiCard>
    </FuiAsync>
  </div>
</template>

<style scoped>
.pkg { padding: var(--fui-sp-4); }
.pkg__row { display: flex; justify-content: space-between; gap: 8px; font-size: var(--fui-fs-sm); padding: 4px 0; }
.pkg__row span { color: var(--fui-text-tertiary); }
.pkg__file { padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.pkg__fpath { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); word-break: break-all; }
.pkg__fmeta { display: flex; gap: 8px; align-items: center; margin-top: 4px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); flex-wrap: wrap; }
.pkg__hash { font-size: 10px; color: var(--fui-text-tertiary); }
.pkg__muted { color: var(--fui-text-tertiary); text-align: center; font-size: var(--fui-fs-sm); }
</style>
