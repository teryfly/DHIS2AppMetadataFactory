<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useOnline } from '@vueuse/core';
import { useI18n } from 'vue-i18n';
// Imported per file rather than through the '@/fui' barrel: the barrel re-exports every primitive,
// and each one carries a `vant/es/x/style` side effect, so a single barrel import here pulled the
// whole FUI library — and its CSS — into the render-blocking entry chunk (Lighthouse
// unused-css-rules). Pages stay behind the barrel; only the shell avoids it.
import FuiCell from '@/fui/FuiCell.vue';import FuiCounterBadge from '@/fui/FuiCounterBadge.vue';
import FuiDialog from '@/fui/FuiDialog.vue';
import FuiField from '@/fui/FuiField.vue';
import FuiIcon from '@/fui/FuiIcon.vue';
import FuiNotice from '@/fui/FuiNotice.vue';
import FuiOverlayPanel from '@/fui/FuiOverlayPanel.vue';
import FuiSearchField from '@/fui/FuiSearchField.vue';
import FuiToastHost from '@/fui/FuiToastHost.vue';
import FuiTopBar from '@/fui/FuiTopBar.vue';
import { isDesktop } from '@/lib/breakpoint';
import { safeReturnTo } from '@/lib/origin';
import { searchAll, type SearchItem, type SearchKind } from '@/lib/search';
import { envClassLabel, stageStatusLabel, stageTypeLabel } from '@/lib/labels';
import { fmtClock } from '@/lib/time';
import { useConnectionStore } from '@/stores/connection';
import { useOperatorStore } from '@/stores/operator';
import { RECENT_TARGETS, useUiStore, type RecentObject } from '@/stores/ui';
import { useAuthStore } from '@/stores/auth';
import { useGatesPending, useNotifications, useOpenQuestions, useProjects, useEnvironments, useRegistry, useStages, useWorkflowOfProject } from '@/api/queries';

const route = useRoute();
const router = useRouter();
const { t, te } = useI18n();
const online = useOnline();
const conn = useConnectionStore();
const operator = useOperatorStore();
const ui = useUiStore();
const auth = useAuthStore();

// Login (M4). Auth is cookie-based; when S1 reports auth enabled and there is no session we show a
// Sign-in (UM-AUTH-001, UM-NET-009). It used to be a modal raised over whatever screen refused the
// request; it is a page now, and the shell's only job is to put the operator there and to bring them
// back. When `authMode` is "off" nothing here happens at all and the app stays in open mode.
//
// One guard covers both ways of arriving: no session yet, and a session that ended while the app was
// open (the 401 watch in `stores/auth.ts` sets `needsLogin`, and this watch follows it). The screen
// the operator was on travels as `?next=`, so a sign-in resumes the work instead of dropping them at
// the home tab.
function toSignIn(): void {
  // Two states must not be redirected away. `bare` covers the sign-in page itself and the single
  // sign-on landing page, which is unauthenticated for exactly as long as the code exchange takes —
  // yanking the operator off it mid-exchange is how a valid login ends up reported as a failure.
  // And `matched.length === 0` is "the router has not resolved the address we were opened at yet":
  // deciding to go sign in then throws away the page the operator asked for, including the `code`
  // sitting in its query.
  if (route.meta.bare || route.matched.length === 0) return;
  const here = route.fullPath;
  void router.replace({ name: 'login', query: here === '/' ? {} : { next: here } });
}

onMounted(async () => {
  // The shell mounts before the first navigation finishes, because the route components are lazy
  // chunks. Ask the router where we are before asking S1 who we are — otherwise a cold deep link
  // (`/login`, `/auth/callback?code=…`) is answered as if it had been opened at `/`.
  await router.isReady();
  try {
    await auth.refresh();
  } catch {
    /* session read failure is surfaced by the toast layer */
  }
  if (auth.needsLogin) toSignIn();
});

watch(() => auth.needsLogin, (need) => {
  if (need) toSignIn();
  else if (route.name === 'login') {
    const next = route.query.next;
    void router.replace(safeReturnTo(typeof next === 'string' ? next : '/'));
  }
});

const headerTitle = computed(() => {
  const key = route.meta.titleKey as string | undefined;
  if (!key) return '';
  // 治理分了节，标题也跟着分节。滚到页面下面时那一句「治理」回答不了「我在哪一节」，而顶栏是唯一
  // 一直在那里的东西。不认识的分节回落到第一节 —— 与页面自己的回落同一个答案。
  if (route.name === 'governance') {
    const wanted = `governance.section.${String(route.params.section ?? 'models')}`;
    return `${t(key)} · ${t(te(wanted) ? wanted : 'governance.section.models')}`;
  }
  return t(key);
});
const showBack = computed(() => !!route.meta.showBack);
// The sign-in page carries no navigation: nothing on it is reachable without the session the page
// is asking for.
const bare = computed(() => !!route.meta.bare);

const pendingGates = useGatesPending();
const openQuestions = useOpenQuestions();
const projects = useProjects(true);
// One row is enough: the badge wants S1's `total` for the unread filter, not a list. Counting the
// newest page here was how the tab came to say "50" to somebody with 200 to read.
const notifications = useNotifications(undefined, { unreadOnly: true, limit: 1 });
// UM-HOME-002/003: the tab counts what the to-do centre lists — gates, open questions, blocked and
// manual-check stages — not the approval queue alone. A review item nobody sees is not a to-do.
const todoCount = computed(() => {
  let count = pendingGates.data.value?.total ?? 0;
  count += openQuestions.data.value?.total ?? 0;
  for (const project of projects.data.value?.items ?? []) {
    if (project.archived) continue;
    for (const stage of project.stages ?? []) {
      if (stage.status === 'BLOCKED' || stage.status === 'MANUAL_CHECK') count += 1;
    }
  }
  return count;
});
const unreadCount = computed(() => Number(notifications.data.value?.total ?? 0));

const tabs = computed(() => [
  { key: 'home', to: '/', icon: 'wap-home-o', label: t('tabs.home'), badge: 0 },
  { key: 'projects', to: '/projects', icon: 'apps-o', label: t('tabs.projects'), badge: 0 },
  { key: 'todo', to: '/todo', icon: 'todo-list-o', label: t('tabs.todo'), badge: todoCount.value },
  { key: 'messages', to: '/notifications', icon: 'chat-o', label: t('tabs.messages'), badge: unreadCount.value },
  { key: 'me', to: '/me', icon: 'manager-o', label: t('tabs.me'), badge: 0 },
]);
const activeTab = computed(() => (route.meta.tab as string) ?? '');

const sideNav = computed(() => [
  { to: '/', icon: 'wap-home-o', label: t('tabs.home') },
  { to: '/start', icon: 'question-o', label: t('start.title') },
  { to: '/projects', icon: 'apps-o', label: t('tabs.projects') },
  { to: '/todo', icon: 'todo-list-o', label: t('tabs.todo') },
  // The same queue, addressed to the one thing an approver actually wants to see: 待办的徽标本来就是
  // S1 报的待批节点数，但没有一个能收藏、能转给同事的地址。`?kind=gate` 就是那句筛选。
  { to: '/todo?kind=gate', icon: 'checked', label: t('nav.approvals') },
  { to: '/notifications', icon: 'chat-o', label: t('tabs.messages') },
  { to: '/environments', icon: 'cluster-o', label: t('environments.title') },
  { to: '/governance', icon: 'shield-o', label: t('governance.title') },
  { to: '/access', icon: 'friends-o', label: t('access.title') },
  { to: '/settings', icon: 'setting-o', label: t('settings.title') },
  { to: '/me', icon: 'manager-o', label: t('tabs.me') },
]);

const searchOpen = ref(false);
const q = ref('');
// `projects` is declared with the to-do count above; the search panel reuses that list.
// UM-NAV-004's four kinds, asked only while the panel is open: the environment list, and the stage
// and registry reads of the project the operator was last in. Stages and registry objects have no
// cross-project endpoint in S1, so those two groups search that project and the panel says so.
const environments = useEnvironments(searchOpen);
// The two project-scoped reads follow the same gate as `environments`: they exist for the search
// panel, not for the shell. Asked always, they re-read a `lastProjectId` the operator left behind
// — a project since deleted answers 403 (S1 hides existence from non-members), and a global read
// error announces itself as a permission toast on every screen. An id that is not in *this*
// operator's project list is not a context; it gets dropped until the panel asks for a real one.
const visibleLastProjectId = computed(() => {
  const stored = ui.state.lastProjectId ?? '';
  if (!stored) return '';
  return (projects.data.value?.items ?? []).some((p) => String(p.id) === stored) ? stored : '';
});
const searchContextId = computed(() => (searchOpen.value ? visibleLastProjectId.value : ''));
const lastWorkflow = useWorkflowOfProject(searchContextId);
const stages = useStages(computed(() => String(lastWorkflow.data.value?.id ?? '')));
const registry = useRegistry(searchContextId);
const groups = computed(() => searchAll(q.value, {
  projects: projects.data.value?.items ?? [],
  stages: stages.data.value?.items ?? [],
  environments: environments.data.value?.items ?? [],
  objects: registry.data.value?.items ?? [],
  projectId: visibleLastProjectId.value,
}));
const recentRows = computed(() => ui.state.recent.map((r) => ({
  ...r,
  label: r.kind === 'project' ? (projects.data.value?.items?.find((p) => String(p.id) === r.id)?.name ?? r.label) : r.label,
})));
// Which group a row belongs to, and what the panel calls it. The search reaches four kinds; the
// memory bar keeps whatever the operator actually visited, so it can also hold a gate, a question,
// a run or an artifact.
const GROUP_TITLES: Record<SearchKind | 'gate' | 'question' | 'run' | 'artifact', string> = {
  project: 'nav.groupProjects',
  stage: 'nav.groupStages',
  environment: 'nav.groupEnvironments',
  object: 'nav.groupObjects',
  gate: 'nav.groupGates',
  question: 'nav.groupQuestions',
  run: 'nav.groupRuns',
  artifact: 'nav.groupArtifacts',
};

function onSearchPick(item: SearchItem): void {
  searchOpen.value = false;
  void router.push(item.to);
}

// The second line is a code S1 answers with (`RUNNING`, `PRODUCTION`), and this console labels those
// one way everywhere else — so it is translated here rather than shown as the wire token.
function subLabel(item: SearchItem): string | undefined {
  if (!item.sub) return undefined;
  if (item.kind === 'stage') return stageStatusLabel(item.sub);
  if (item.kind === 'environment') return envClassLabel(item.sub);
  return item.sub;
}

function onRecentPick(kind: RecentObject['kind'], id: string): void {
  searchOpen.value = false;
  void router.push(RECENT_TARGETS[kind](id));
}

const showNameDialog = ref(false);
const nameInput = ref('');
// The first tick runs before `/auth/session` comes back, so the prompt must be withdrawn once the
// account is known rather than left open over a signed-in console.
watch(
  () => [operator.hasOperator, auth.authenticated] as const,
  ([has, signedIn]) => {
    if (signedIn) showNameDialog.value = false;
    else if (!has && !sessionStorage.getItem('s5m:name-prompted')) showNameDialog.value = true;
  },
  { immediate: true },
);
function saveName(): void {
  if (nameInput.value.trim()) {
    operator.setName(nameInput.value);
    sessionStorage.setItem('s5m:name-prompted', '1');
    showNameDialog.value = false;
  }
}
</script>

<template>
  <div class="shell">
    <aside v-if="isDesktop && !bare" class="shell__side">
      <div class="shell__brand">{{ t('app.brand') }}</div>
      <router-link v-for="n in sideNav" :key="n.to" :to="n.to" class="shell__side-item">
        <FuiIcon :name="n.icon" size="20" /><span>{{ n.label }}</span>
      </router-link>
    </aside>

    <div class="shell__main">
      <FuiTopBar v-if="!bare" :title="headerTitle" :show-back="showBack" class="shell__topbar">
        <template #right>
          <div class="shell__topactions">
            <FuiIcon name="manager-o" size="20" :label="t('nav.identity')" @click="router.push(auth.authenticated ? '/access' : '/login')" />
            <FuiIcon name="search" size="20" :label="t('nav.search')" @click="searchOpen = true" />
          </div>
        </template>
      </FuiTopBar>

      <transition name="fade">
        <FuiNotice v-if="!online" :text="t('offline.banner', { time: fmtClock(conn.lastLoadedAt) })" />
        <!-- UM-NET-004: the stream dropping to polling keeps the screen working, but it no longer
             moves by itself. Saying so is the difference between "the number is stale" and
             "the number is wrong". -->
        <FuiNotice v-else-if="conn.status === 'polling'" :text="t('banner.polling')" tone="info" />
      </transition>

      <main class="shell__content" role="main">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>

      <!-- Navigation between top-level views, not a tablist: the children are <router-link> <a>
           elements carrying aria-current, which role="tablist" would forbid (aria-required-children). -->
      <nav v-if="!isDesktop && !bare" class="shell__tabbar" :aria-label="t('app.brand')">
        <router-link
          v-for="tab in tabs"
          :key="tab.key"
          :to="tab.to"
          class="shell__tab"
          :class="{ 'shell__tab--active': activeTab === tab.key }"
        >
          <FuiCounterBadge :content="tab.badge > 0 ? tab.badge : ''" :show-zero="false">
            <FuiIcon :name="tab.icon" size="22" />
          </FuiCounterBadge>
          <span class="shell__tab-label">{{ tab.label }}</span>
        </router-link>
      </nav>
    </div>

    <FuiOverlayPanel v-model:show="searchOpen" position="top" :style="{ padding: '8px 12px' }">
      <FuiSearchField v-model="q" :placeholder="t('nav.searchPlaceholder')" show-action shape="round" @cancel="searchOpen = false" />

      <template v-if="q.trim()">
        <template v-for="group in groups" :key="group.kind">
          <div class="shell__recent-title">{{ t(GROUP_TITLES[group.kind]) }}</div>
          <FuiCell
            v-for="item in group.items"
            :key="`${item.kind}-${item.id}`"
            :title="item.kind === 'stage' ? stageTypeLabel(item.label) : item.label"
            :label="subLabel(item)"
            is-link
            @click="onSearchPick(item)"
          />
        </template>
        <p v-if="!groups.length" class="shell__nores">{{ t('nav.noResults') }}</p>
        <!-- Stages and registry objects only exist inside one project, and S1 has no endpoint that
             lists them across projects. Saying which project was searched is what keeps a "no hits"
             from reading as "there are none". -->
        <p v-else-if="!visibleLastProjectId" class="shell__nores">{{ t('nav.searchScopeHint') }}</p>
      </template>

      <template v-else>
        <div class="shell__recent-title">{{ t('nav.recent') }}</div>
        <FuiCell
          v-for="r in recentRows"
          :key="`${r.kind}-${r.id}`"
          :title="r.kind === 'stage' ? stageTypeLabel(r.label) : r.label"
          :label="t(GROUP_TITLES[r.kind])"
          is-link
          @click="onRecentPick(r.kind, r.id)"
        />
        <p v-if="!recentRows.length" class="shell__nores">{{ t('nav.noRecent') }}</p>
      </template>
    </FuiOverlayPanel>

    <FuiDialog v-model:show="showNameDialog" :title="t('operator.who')" show-cancel-button :confirm-button-text="t('common.save')" @confirm="saveName">
      <div class="shell__name">
        <p class="shell__name-why">{{ t('operator.why') }}</p>
        <FuiField v-model="nameInput" :placeholder="t('operator.placeholder')" />
      </div>
    </FuiDialog>

    <FuiToastHost />
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; background: var(--fui-bg); }
.shell__side { display: none; }
@media (min-width: 1024px) {
  .shell__side { display: flex; flex-direction: column; width: 220px; background: var(--fui-surface); border-right: 1px solid var(--fui-border); padding: var(--fui-sp-4) var(--fui-sp-2); position: sticky; top: 0; height: 100vh; }
  .shell__brand { font-weight: 700; font-size: var(--fui-fs-lg); padding: 0 var(--fui-sp-3) var(--fui-sp-4); }
  .shell__side-item { display: flex; align-items: center; gap: 12px; padding: 12px var(--fui-sp-3); border-radius: var(--fui-radius-btn); color: var(--fui-text-secondary); text-decoration: none; }
  .shell__side-item:hover, .shell__side-item.router-link-active { background: var(--fui-info-bg); color: var(--fui-primary); }
}
.shell__main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.shell__content { flex: 1; overflow-x: hidden; padding-bottom: var(--fui-safe-bottom); max-width: 900px; width: 100%; margin: 0 auto; }
.shell__tabbar { position: sticky; bottom: 0; display: flex; background: var(--fui-surface); border-top: 1px solid var(--fui-border); padding-bottom: var(--fui-safe-bottom); z-index: 30; }
.shell__tab { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 8px 0; color: var(--fui-text-secondary); text-decoration: none; min-height: var(--fui-tap-min); }
.shell__tab--active { color: var(--fui-primary); }
.shell__tab-label { font-size: var(--fui-fs-xs); }
.shell__topbar { position: sticky; top: 0; z-index: 25; }
.shell__recent-title { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); padding: 4px 8px; }
.shell__nores { text-align: center; color: var(--fui-text-tertiary); padding: var(--fui-sp-4); }
.shell__name { padding: var(--fui-sp-4); }
.shell__name-why { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 0 0 var(--fui-sp-3); }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.shell__topactions { display: flex; gap: 14px; align-items: center; }
.shell__login-busy { display: flex; justify-content: center; padding: 8px; }
</style>
