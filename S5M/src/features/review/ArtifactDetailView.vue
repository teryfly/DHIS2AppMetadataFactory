<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiReadOnlyViewer, FuiNotice, FuiFilterBar, FuiDiffViewer, FuiBottomSheet, FuiField, FuiButton, FuiActionButton } from '@/fui';
import { useArtifact, useArtifactContent, useArtifactLineage, useArtifactDiff, useAllowedActions } from '@/api/queries';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { usePurgeArtifact } from '@/api/mutations';
import { AppError } from '@/api/errors';
import { copyText, currentUrl } from '@/lib/clipboard';
import { artifactKindLabel, capabilityLabel, classificationLabel } from '@/lib/labels';
import { formatBytes, looksTextual } from '@/lib/media';
import { pushSuccess } from '@/lib/toast';
import { apiPrefix } from '@/config/runtime';

const { t, te } = useI18n();
const route = useRoute();
const id = computed(() => String(route.params.id ?? ''));
/** The list that linked here knows the uploaded filename; the artifact record itself does not. */
const givenName = computed(() => String(route.query.name ?? ''));
const a = useArtifact(id);
const node = computed(() => a.data.value as Record<string, unknown> | undefined);
const purged = computed(() => Boolean(node.value?.contentPurged));
const mediaType = computed(() => String(node.value?.mediaType ?? ''));
const sizeText = computed(() => formatBytes(Number(node.value?.size ?? 0)));
/**
 * Text goes to the viewer, everything else to S1's download link.
 *
 * Reading a .docx as text is not a preview, it is a wall of replacement characters, and the page
 * used to show exactly that with no explanation. The bytes are also not fetched at all now: the
 * `enabled` flag below keeps a 20 MB office file out of a string this screen cannot render.
 */
const textual = computed(() => !purged.value && looksTextual(mediaType.value, givenName.value));

const content = useArtifactContent(id, textual);
const lineage = useArtifactLineage(id);
const prevId = computed(() => String(node.value?.prevVersionId ?? ''));
const diff = useArtifactDiff(id, prevId);

/** A refused read is an answer the page has to show: 403 需要授权、410 已清除, never an empty box. */
const contentProblem = computed(() => {
  if (!content.isError.value) return '';
  const err = content.error.value;
  if (err instanceof AppError) return te(`errors.${err.errorCode}`) ? t(`errors.${err.errorCode}`) : err.message;
  return String((err as Error | null)?.message ?? err);
});

const tab = ref<'content' | 'diff' | 'lineage'>('content');
const diffLines = computed<Record<string, unknown>[]>(() => (diff.data.value as unknown as { lines?: Record<string, unknown>[] })?.lines ?? []);
const tabs = computed(() => [
  { value: 'content', label: t('review.content') },
  { value: 'diff', label: t('common.viewRaw') + ' Δ' },
  { value: 'lineage', label: t('review.lineage') },
]);

// UM-REV-001 asks for two things here — 下载 and 复制链接 — and one control that says "下载 / 复制
// 链接" while doing only the second is the failure this page had: the operator pressed 下载 and got a
// clipboard. The bytes have their own address (CP-UI-016: `?download=true` names the file), so the
// pair is now two honest controls, and whether the first exists is S1's answer (UM-BR-01), not ours.
const workflowId = computed(() => String(node.value?.workflowId ?? ''));
const actions = useAllowedActions(workflowId);
const downloadGate = computed(() => resolveAvailability(indexActions(actions.data.value), ACTION.artifactDownload));
const downloadUrl = computed(() => `${apiPrefix()}/artifacts/${encodeURIComponent(id.value)}/content?download=true`);
const downloadReason = computed(() => (downloadGate.value.reasonCode ? t(`actionReason.${downloadGate.value.reasonCode}`) : t('actionReason.unknown')));

function copyLink(): void {
  void copyText(currentUrl()).then(() => pushSuccess(t('common.copied')));
}

// FR-DATA-008 keeps the record and drops the bytes, so this is the one write on the screen that
// cannot be undone: UM-P-04 and §6.2 require it to be confirmed by typing, not by a swipe.
const purgeSheet = ref(false);
const purgeTyped = ref('');
const purge = usePurgeArtifact();

function doPurge(): void {
  purge.mutate(id.value, {
    onSuccess: () => {
      purgeSheet.value = false;
      purgeTyped.value = '';
      pushSuccess(t('review.purged'));
    },
  });
}
</script>

<template>
  <div class="ad">
    <FuiAsync :loading="a.isLoading.value" :error="a.isError.value" @retry="a.refetch()">
      <template v-if="node">
        <FuiCard>
          <template #title>{{ artifactKindLabel(String(node.type ?? '')) }}</template>
          <template #extra><FuiStatusTag tone="info" :label="String(node.version ?? '')" /></template>
          <p v-if="givenName || sizeText" class="ad__name">
            {{ givenName || id }}<span v-if="sizeText"> · {{ sizeText }}</span><span v-if="mediaType"> · {{ mediaType }}</span>
          </p>
          <div class="ad__hash">{{ String(node.contentHash ?? '').slice(0, 24) }}</div>
          <div class="ad__meta">
            <span>{{ capabilityLabel(String(node.creatorSubsystem ?? '')) }}</span>
            <span>{{ classificationLabel(String(node.dataClassification ?? '')) }}</span>
          </div>
          <FuiNotice v-if="purged" :text="t('review.purged')" tone="warning" />
          <div class="ad__acts">
            <a v-if="!purged && downloadGate.available" class="ad__dl" :href="downloadUrl">{{ t('review.download') }}</a>
            <p v-else-if="!purged" class="ad__muted">{{ downloadReason }}</p>
            <button class="ad__btn" @click="copyLink">{{ t('review.copyLink') }}</button>
            <FuiActionButton
              :action="ACTION.artifactPurge"
              :workflow-id="workflowId"
              :target-id="id"
              size="small"
              variant="danger"
              :label="t('review.purge')"
              :disabled="purged"
              @click="purgeSheet = true"
            />
          </div>
        </FuiCard>

        <FuiFilterBar v-model="tab" :options="tabs" />

        <FuiCard v-if="tab === 'content'" :title="t('review.content')">
          <!-- The banner above already states the purge; repeating it would put one fact on screen
               twice, and the card has no content to render in that state. -->
          <p v-if="purged" class="ad__muted">{{ t('review.purgedBody') }}</p>
          <FuiNotice v-else-if="!textual" tone="info" :text="t('review.binary', { type: mediaType || '—', size: sizeText || '—' })" />
          <FuiNotice v-else-if="contentProblem" tone="danger" :text="contentProblem" />
          <FuiReadOnlyViewer v-else :content="content.data.value ?? ''" />
        </FuiCard>

        <FuiCard v-else-if="tab === 'diff'" :title="`${t('common.viewRaw')}`">
          <p v-if="!prevId" class="ad__muted">{{ t('review.versions') }} — {{ t('common.notFound') }}</p>
          <FuiDiffViewer v-else :lines="diffLines" :empty-hint="t('common.notFound')" />
        </FuiCard>

        <FuiCard v-else :title="t('review.lineage')">
          <FuiDiffViewer :lines="[]" :empty-hint="t('common.notFound')" v-if="!lineage.data.value" />
          <pre v-else class="ad__lin" role="region" tabindex="0" :aria-label="t('review.lineage')">{{ JSON.stringify(lineage.data.value, null, 2) }}</pre>
        </FuiCard>
      </template>
    </FuiAsync>

    <FuiBottomSheet v-model="purgeSheet" :title="t('review.purge')">
      <p class="ad__warn">{{ t('review.purgeConfirm') }}</p>
      <p class="ad__hash">{{ id }}</p>
      <FuiField v-model="purgeTyped" :placeholder="id" />
      <template #footer>
        <FuiButton variant="danger" block :label="t('common.confirm')" :disabled="purgeTyped !== id" :loading="purge.isPending.value" @click="doPurge" />
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.ad { padding: var(--fui-sp-4); }
.ad__hash { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.ad__name { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 4px 0 0; word-break: break-all; }
.ad__meta { display: flex; gap: 12px; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin-top: 6px; }
.ad__acts { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
.ad__btn { padding: 0 12px; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); color: var(--fui-primary); font-size: var(--fui-fs-sm); min-height: var(--fui-tap-min); min-width: var(--fui-tap-min); }
.ad__dl { display: inline-flex; align-items: center; justify-content: center; padding: 0 12px; min-height: var(--fui-tap-min); min-width: var(--fui-tap-min); border: 1px solid var(--fui-primary); border-radius: var(--fui-radius-btn); color: var(--fui-primary); font-size: var(--fui-fs-sm); text-decoration: none; }
.ad__muted { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); margin: 0; }
.ad__warn { color: var(--fui-danger); font-size: var(--fui-fs-sm); }
.ad__lin { white-space: pre-wrap; font-size: var(--fui-fs-xs); overflow: auto; max-height: 40vh; }
</style>
