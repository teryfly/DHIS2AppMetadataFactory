import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router';
import { useUiStore } from '@/stores/ui';

declare module 'vue-router' {
  interface RouteMeta {
    /** Which bottom tab this route belongs to (drives tab highlight + back target). */
    tab?: 'home' | 'projects' | 'todo' | 'messages' | 'me';
    titleKey?: string;
    showBack?: boolean;
    /**
     * No chrome: the tab bar, the side nav and the identity/search actions stay out of the way.
     * Only the sign-in page uses it — an unfinished app should not offer its navigation to somebody
     * who cannot use any of it yet.
     */
    bare?: boolean;
  }
}

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: () => import('@/features/home/HomeView.vue'), meta: { tab: 'home', titleKey: 'home.greeting' } },
  // UM-AUTH-001 / UM-NET-009: sign-in is a page, not a modal over the screen that refused you.
  { path: '/login', name: 'login', component: () => import('@/features/access/SignInView.vue'), meta: { titleKey: 'auth.signInTitle', bare: true } },
  // UM-AUTH-001: where the organisation's sign-in page returns to. `bare` for the same reason as
  // /login, and because the shell's "no session → sign in" watch would otherwise pull the operator
  // off this page during the one second the code is being exchanged.
  { path: '/auth/callback', name: 'oidc-callback', component: () => import('@/features/access/OidcCallbackView.vue'), meta: { titleKey: 'auth.signInTitle', bare: true } },
  { path: '/todo', name: 'todo', component: () => import('@/features/todo/TodoView.vue'), meta: { tab: 'todo', titleKey: 'todo.title' } },
  { path: '/notifications', name: 'notifications', component: () => import('@/features/notifications/NotificationsView.vue'), meta: { tab: 'messages', titleKey: 'notifications.title' } },
  { path: '/me', name: 'me', component: () => import('@/features/me/MeView.vue'), meta: { tab: 'me', titleKey: 'me.title' } },
  // UM-SET-001. Deliberately not a sixth bottom tab: five is the readable maximum on a phone, and
  // this page is visited a handful of times rather than constantly.
  { path: '/start', name: 'start', component: () => import('@/features/start/StartView.vue'), meta: { titleKey: 'start.title', showBack: true } },

  { path: '/projects', name: 'projects', component: () => import('@/features/projects/ProjectsView.vue'), meta: { tab: 'projects', titleKey: 'projects.title' } },
  { path: '/projects/new', name: 'project-new', component: () => import('@/features/projects/NewProjectView.vue'), meta: { titleKey: 'wizard.title', showBack: true } },
  { path: '/projects/:id', name: 'project-detail', component: () => import('@/features/projects/ProjectDetailView.vue'), meta: { titleKey: 'project.timeline', showBack: true } },
  { path: '/projects/:id/settings', name: 'project-settings', component: () => import('@/features/projects/ProjectSettingsView.vue'), meta: { titleKey: 'project.settings', showBack: true } },
  // M4 (CP-UI-023): the three project-scoped authorization screens. Project paths rather than tabs on
  // /access, because every one of them is about *this* project and S1 answers their rights per
  // project (`domain/actions.py:project_actions()`), so each page needs the project id in the URL.
  { path: '/projects/:id/members', name: 'project-members', component: () => import('@/features/project/ProjectMembersView.vue'), meta: { titleKey: 'admin.membersTitle', showBack: true } },
  { path: '/projects/:id/grants', name: 'project-grants', component: () => import('@/features/project/ArtifactGrantsView.vue'), meta: { titleKey: 'admin.grantsTitle', showBack: true } },
  { path: '/projects/:id/exemptions', name: 'project-exemptions', component: () => import('@/features/project/ProductionExemptionsView.vue'), meta: { titleKey: 'admin.exemptionsTitle', showBack: true } },

  { path: '/stages/:id', name: 'stage', component: () => import('@/features/stage/StageView.vue'), meta: { titleKey: 'stage.workspace', showBack: true } },
  { path: '/questions/:id', name: 'question', component: () => import('@/features/questions/QuestionDetailView.vue'), meta: { titleKey: 'questions.title', showBack: true } },
  { path: '/gates/:id', name: 'gate', component: () => import('@/features/gates/GateView.vue'), meta: { titleKey: 'gate.title', showBack: true } },

  { path: '/projects/:id/inputs', name: 'inputs', component: () => import('@/features/inputs/InputsView.vue'), meta: { titleKey: 'inputs.title', showBack: true } },
  { path: '/projects/:id/inputs/:artifactId/column-mapping', name: 'column-mapping', component: () => import('@/features/inputs/ColumnMappingView.vue'), meta: { titleKey: 'inputs.reviewMapping', showBack: true } },
  { path: '/projects/:id/inputs/:artifactId/llm-log', name: 'llm-log', component: () => import('@/features/inputs/LlmLogView.vue'), meta: { titleKey: 'llmLog.title', showBack: true } },
  // 需求条目: the hand-authored half of the requirement list. Upload + parse produce the same rows,
  // so this is the screen that edits what the parser extracted — and what the operator typed.
  { path: '/projects/:id/requirements', name: 'project-requirements', component: () => import('@/features/requirements/RequirementsView.vue'), meta: { titleKey: 'reqItems.title', showBack: true } },

  { path: '/projects/:id/artifacts', name: 'artifacts', component: () => import('@/features/review/ArtifactsView.vue'), meta: { titleKey: 'review.artifacts', showBack: true } },
  { path: '/artifacts/:id', name: 'artifact-detail', component: () => import('@/features/review/ArtifactDetailView.vue'), meta: { titleKey: 'review.content', showBack: true } },
  { path: '/projects/:id/metadata', name: 'metadata', component: () => import('@/features/review/MetadataView.vue'), meta: { titleKey: 'review.metadata', showBack: true } },
  { path: '/projects/:id/issues', name: 'issues', component: () => import('@/features/review/ValidationIssuesView.vue'), meta: { titleKey: 'review.issues', showBack: true } },
  { path: '/projects/:id/graph', name: 'graph', component: () => import('@/features/review/GraphView.vue'), meta: { titleKey: 'graph.title', showBack: true } },
  { path: '/projects/:id/api', name: 'api-review', component: () => import('@/features/review/ApiReviewView.vue'), meta: { titleKey: 'api.title', showBack: true } },
  // UM-CAP-003: the desktop document view has its own URL so a reviewer can link to it. Lazy like
  // every other heavy capability — the document kernel and js-yaml stay out of the first screen.
  { path: '/projects/:id/api/doc', name: 'api-doc', component: () => import('@/features/review/ApiDocView.vue'), meta: { titleKey: 'api.doc.title', showBack: true } },
  { path: '/projects/:id/package', name: 'package', component: () => import('@/features/review/PackageView.vue'), meta: { titleKey: 'pkg.title', showBack: true } },
  // CP-UI-021: the mode-B development package. A different deliverable from the build package above
  // — that one goes to an environment, this one is what a frontend team works from.
  { path: '/projects/:id/handoff', name: 'handoff', component: () => import('@/features/review/HandoffView.vue'), meta: { titleKey: 'handoff.title', showBack: true } },

  { path: '/workflows/:id/deployments', name: 'deployments', component: () => import('@/features/deployments/DeploymentsView.vue'), meta: { titleKey: 'deployments.title', showBack: true } },
  { path: '/runs/:id', name: 'run', component: () => import('@/features/deployments/RunConsoleView.vue'), meta: { titleKey: 'deployments.title', showBack: true } },

  { path: '/projects/:id/change-requests', name: 'change-requests', component: () => import('@/features/changeRequests/ChangeRequestsView.vue'), meta: { titleKey: 'changeRequests.title', showBack: true } },
  { path: '/projects/:id/audit', name: 'audit', component: () => import('@/features/audit/AuditView.vue'), meta: { titleKey: 'audit.title', showBack: true } },

  { path: '/environments', name: 'environments', component: () => import('@/features/environments/EnvironmentsView.vue'), meta: { titleKey: 'environments.title', showBack: true } },
  { path: '/environments/:key', name: 'environment-detail', component: () => import('@/features/environments/EnvironmentDetailView.vue'), meta: { titleKey: 'environments.title', showBack: true } },
  // One section on screen at a time, addressed by the URL: the page used to be eight cards deep and
  // every entry point — the guide, search, 我的 — dumped the operator at its top.
  { path: '/governance', redirect: '/governance/models' },
  { path: '/governance/:section', name: 'governance', component: () => import('@/features/governance/GovernanceView.vue'), meta: { titleKey: 'governance.title', showBack: true } },
  { path: '/access', name: 'access', component: () => import('@/features/access/AccessView.vue'), meta: { titleKey: 'access.title', showBack: true } },
  { path: '/settings', name: 'settings', component: () => import('@/features/settings/SettingsView.vue'), meta: { titleKey: 'settings.title', showBack: true } },

  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/features/common/NotFound.vue'), meta: { showBack: true } },
];

// Capability harnesses (UM-AC-17/18) are mounted only for development and for the measurement
// build (`VITE_DEV_HARNESS=1`), so a normal production bundle never ships CodeMirror or the graph
// benchmark page.
if (import.meta.env.DEV || import.meta.env.VITE_DEV_HARNESS === '1') {
  routes.splice(routes.length - 1, 0,
    { path: '/dev/json-editor', name: 'dev-json-editor', component: () => import('@/features/dev/DevJsonEditorView.vue'), meta: { showBack: true } },
    { path: '/dev/graph-bench', name: 'dev-graph-bench', component: () => import('@/features/dev/GraphBenchView.vue'), meta: { showBack: true } },
  );
}

export const router = createRouter({
  history: createWebHistory(),
  routes,
  // Restore scroll on back/forward, top on new navigation (UM-NAV-005).
  scrollBehavior(_to, _from, saved) {
    return saved ?? { top: 0 };
  },
});

// Remember what the operator opened, for the search panel's second half (UM-NAV-004: 最近访问的 10
// 个对象). Every kind the panel can list has to be written here — a kind that is never recorded is a
// row that never appears, and `RECENT_TARGETS` in `stores/ui.ts` is where each one's route lives.
router.afterEach((to) => {
  if (to.name === 'project-detail' && typeof to.params.id === 'string') {
    useUiStore().setLastProject(to.params.id);
    useUiStore().rememberRecent({ kind: 'project', id: to.params.id, label: to.params.id });
  } else if (typeof to.params.id === 'string' && (to.name === 'gate' || to.name === 'run' || to.name === 'question' || to.name === 'stage' || to.name === 'artifact-detail')) {
    const kind = to.name === 'artifact-detail' ? 'artifact' : to.name as 'gate' | 'run' | 'question' | 'stage';
    useUiStore().rememberRecent({ kind, id: to.params.id as string, label: to.params.id as string });
  } else if (typeof to.params.key === 'string' && to.name === 'environment-detail') {
    useUiStore().rememberRecent({ kind: 'environment', id: to.params.key, label: to.params.key });
  }
});
