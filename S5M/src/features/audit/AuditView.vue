<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiButton, FuiField, FuiNotice } from '@/fui';
import { useAuditFeed } from '@/api/queries';
import { apiSend } from '@/api/http';
import { fmtRelTime } from '@/lib/time';
import { usePrefsStore } from '@/stores/prefs';
import { pushSuccess } from '@/lib/toast';

const { t } = useI18n();
const route = useRoute();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));
const projectId = computed(() => String(route.params.id ?? ''));

const filters = reactive({ operator: '', event: '' });

/**
 * S5IF-G04: the trail is one cursor walk, and the walking is `useInfiniteQuery`'s job rather than
 * this screen's — a hand-rolled page array is how a re-fetch of the window already on screen ends up
 * appended to itself.
 */
const audit = useAuditFeed(projectId, () => ({
  operatorName: filters.operator || undefined,
  eventType: filters.event || undefined,
}));
const list = computed(() => audit.data.value?.pages.flatMap((page) => page.items ?? []) as Record<string, unknown>[] ?? []);
const total = computed(() => Number(audit.data.value?.pages[0]?.total ?? list.value.length));
const hasMore = computed(() => !!audit.hasNextPage.value);
function loadMore(): void {
  void audit.fetchNextPage();
}

const verifying = ref(false);
// The answer is the point of the call: `AuditVerifyView` says `verified` / `brokenAt`. Discarding it
// and pushing "chain intact" unconditionally made a broken chain look green on the one screen whose
// job is to notice that.
type VerifyResult = {
  verified?: boolean;
  brokenAt?: number | null;
  firstSequence?: number | null;
  lastSequence?: number | null;
  recordCount?: number;
};
const verifyResult = ref<VerifyResult | null>(null);
async function verify(): Promise<void> {
  verifying.value = true;
  verifyResult.value = null;
  try {
    verifyResult.value = await apiSend<VerifyResult>('POST', '/audit/verify', {});
    if (verifyResult.value?.verified) pushSuccess(t('audit.verifyOk'));
  } finally {
    verifying.value = false;
  }
}
</script>

<template>
  <div class="au">
    <FuiCard flat>
      <div class="au__filters">
        <FuiField v-model="filters.operator" :label="t('audit.filterOperator')" class="au__f" />
        <FuiField v-model="filters.event" :label="t('audit.filterEvent')" class="au__f" />
      </div>
      <FuiButton size="small" :label="t('audit.verifyChain')" :loading="verifying" @click="verify" />
      <FuiNotice
        v-if="verifyResult"
        :tone="verifyResult.verified ? 'info' : 'danger'"
        :text="verifyResult.verified
          ? t('audit.chainRange', { from: verifyResult.firstSequence ?? '—', to: verifyResult.lastSequence ?? '—', count: verifyResult.recordCount ?? 0 })
          : t('audit.chainBroken', { seq: verifyResult.brokenAt ?? '—' })"
      />
    </FuiCard>

    <FuiAsync
      :loading="audit.isLoading.value"
      :error="audit.isError.value"
      :empty="!list.length"
      :empty-title="t('audit.title')"
    >
      <FuiCard v-for="(r, i) in list" :key="i">
        <template #title><FuiStatusTag tone="info" :label="String(r.eventType ?? '')" /></template>
        <div class="au__row">{{ r.summary ?? r.description ?? '' }}</div>
        <div class="au__meta">
          <span>{{ r.operatorName }}</span>
          <span>{{ fmtRelTime(r.occurredAt as string, lang) }}</span>
        </div>
      </FuiCard>
      <p v-if="list.length" class="au__count">{{ list.length }} {{ t('common.of') }} {{ total }}</p>
      <FuiButton v-if="hasMore" size="small" block :label="t('common.loadMore')" :loading="audit.isFetching.value" @click="loadMore" />
      <p class="au__note">{{ t('audit.traceDesktopSoon') }}</p>
    </FuiAsync>
  </div>
</template>

<style scoped>
.au { padding: var(--fui-sp-4); }
.au__filters { display: flex; gap: 8px; margin-bottom: 8px; }
.au__f { flex: 1; }
.au__row { font-size: var(--fui-fs-sm); margin-top: 4px; }
.au__meta { display: flex; justify-content: space-between; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 6px; }
.au__note { text-align: center; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.au__count { margin: 8px 0 4px; text-align: center; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
