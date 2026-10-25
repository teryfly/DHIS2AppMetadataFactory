<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiButton, FuiAsync, FuiFilterBar } from '@/fui';
import { useNotificationFeed, useNotifications } from '@/api/queries';
import { useMarkNotificationRead } from '@/api/mutations';
import { usePrefsStore } from '@/stores/prefs';
import { fmtRelTime } from '@/lib/time';

const { t } = useI18n();
const router = useRouter();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));

/**
 * S5IF-G04: the feed is one cursor/offset walk and the pages are `useInfiniteQuery`'s to keep. What
 * the screen owes the operator is the number S1 counted, not the number of rows that happened to
 * arrive — the count beside the filter has to agree with the badge in the tab bar.
 */
const feed = useNotificationFeed();
const total = computed(() => Number(feed.data.value?.pages[0]?.total ?? 0));
const hasMore = computed(() => !!feed.hasNextPage.value);
function loadMore(): void {
  void feed.fetchNextPage();
}
// The badge is S1's count of unread, not a count of the rows this page happens to hold.
const unread = useNotifications(undefined, { unreadOnly: true, limit: 1 });
const markRead = useMarkNotificationRead();
const tab = ref('all');

interface Rec {
  id: string;
  type?: string | null;
  read: boolean;
  message?: string | null;
  createdAt?: string | null;
  projectId?: string | null;
  objectRef?: Record<string, unknown> | null;
}

function groupOf(type?: string | null): string {
  const s = type ?? '';
  if (s.startsWith('GATE')) return 'approval';
  if (s.startsWith('QUESTION')) return 'question';
  if (s.startsWith('EXECUTION')) return 'execution';
  return 'system';
}
function typeLabel(type?: string | null): string {
  if (!type) return '';
  const key = 'notifications.types.' + type;
  return t(key) === key ? type : t(key);
}

const all = computed<Rec[]>(() => (feed.data.value?.pages ?? []).flatMap((page) => (page.items ?? []) as unknown as Rec[]));
const filtered = computed(() => (tab.value === 'all' ? all.value : all.value.filter((n) => groupOf(n.type) === tab.value)));
const unreadCount = computed(() => Number(unread.data.value?.total ?? 0));

const filterOptions = computed(() => [
  { value: 'all', label: t('common.all'), count: all.value.length },
  { value: 'approval', label: t('notifications.groupApproval') },
  { value: 'question', label: t('notifications.groupQuestion') },
  { value: 'execution', label: t('notifications.groupExecution') },
  { value: 'system', label: t('notifications.groupSystem') },
]);

function routeFor(n: Rec): string {
  const ref = n.objectRef ?? {};
  const kind = String(ref.type ?? ref.kind ?? '');
  const targetId = String(ref.id ?? ref.targetId ?? '');
  if (targetId && kind === 'gate') return '/gates/' + targetId;
  if (targetId && kind === 'question') return '/questions/' + targetId;
  if (targetId && kind === 'stage') return '/stages/' + targetId;
  if (targetId && (kind === 'run' || kind === 'deployment_run')) return '/runs/' + targetId;
  if (targetId && kind === 'artifact') return '/artifacts/' + targetId;
  if (n.projectId) return '/projects/' + n.projectId;
  return '/notifications';
}
function open(n: Rec): void {
  if (!n.read) markRead.mutate(n.id);
  router.push(routeFor(n));
}
async function markAll(): Promise<void> {
  await Promise.all(all.value.filter((n) => !n.read).map((n) => markRead.mutateAsync(n.id)));
}
</script>

<template>
  <div class="ntf">
    <FuiFilterBar v-model="tab" :options="filterOptions" />
    <div class="ntf__bar">
      <span>{{ t('notifications.unread') }}: {{ unreadCount }}</span>
      <FuiButton size="small" :disabled="!unreadCount" :label="t('notifications.markAllRead')" @click="markAll" />
    </div>

    <FuiAsync :loading="feed.isLoading.value" :empty="!filtered.length" :empty-title="t('notifications.title')" :empty-hint="t('notifications.empty')">
      <FuiCard v-for="n in filtered" :key="n.id" clickable @click="open(n)">
        <template #title><span :class="{ 'ntf--unread': !n.read }">{{ typeLabel(n.type) }}</span></template>
        <div class="ntf__msg">{{ n.message ?? '' }}</div>
        <div class="ntf__time">{{ fmtRelTime(n.createdAt, lang) }}</div>
      </FuiCard>
      <p class="ntf__count">{{ t('notifications.shownOf', { shown: all.length, total }) }}</p>
      <FuiButton v-if="hasMore" size="small" block :label="t('common.loadMore')" :loading="feed.isFetching.value" @click="loadMore" />
    </FuiAsync>
  </div>
</template>

<style scoped>
.ntf { padding: var(--fui-sp-2) 0; }
.ntf__bar { display: flex; justify-content: space-between; align-items: center; padding: var(--fui-sp-2) var(--fui-sp-4); font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.ntf__msg { font-size: var(--fui-fs-sm); }
.ntf__time { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 6px; }
.ntf--unread { font-weight: 700; }
.ntf__count { margin: 8px var(--fui-sp-4) 4px; text-align: center; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
