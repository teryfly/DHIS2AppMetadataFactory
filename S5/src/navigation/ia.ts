import type { ShellIconName } from '@/components/shellIcons';
import type { IconName } from '@/components/icons';

export interface GlobalNavItem {
  to: string;
  labelKey: string;
  icon: ShellIconName;
  end?: boolean;
  matchPrefix?: string[];
  badge?: 'approvals';
}

export interface GlobalNavGroup {
  labelKey: string;
  items: GlobalNavItem[];
}

/** Primary navigation: three groups instead of ten equally-weighted rows. */
export const GLOBAL_NAV: GlobalNavGroup[] = [
  {
    labelKey: 'nav.groupWork',
    items: [
      { to: '/', labelKey: 'nav.dashboard', icon: 'home', end: true },
      { to: '/approvals', labelKey: 'nav.approvals', icon: 'inbox', badge: 'approvals' },
      { to: '/getting-started', labelKey: 'nav.setup', icon: 'rocket' },
    ],
  },
  {
    labelKey: 'nav.groupProjects',
    items: [{ to: '/projects', labelKey: 'nav.projects', icon: 'folder' }],
  },
  {
    labelKey: 'nav.groupPlatform',
    items: [
      { to: '/environments', labelKey: 'nav.environments', icon: 'server' },
      { to: '/governance', labelKey: 'nav.governance', icon: 'shield' },
      { to: '/users', labelKey: 'nav.accounts', icon: 'users', matchPrefix: ['/users', '/roles'] },
      { to: '/settings', labelKey: 'nav.settings', icon: 'settings', end: true },
    ],
  },
];

export function isGlobalNavItemActive(item: GlobalNavItem, pathname: string): boolean {
  if (item.end) return pathname === item.to || (item.to !== '/' && pathname.startsWith(`${item.to}/`));
  const prefixes = item.matchPrefix ?? [item.to];
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

export type ProjectSectionKey = 'overview' | 'design' | 'artifacts' | 'delivery' | 'manage';

export interface ProjectSubTab {
  key: string;
  labelKey: string;
  segments: string[];
  landing: string;
}

export interface ProjectSection {
  key: ProjectSectionKey;
  labelKey: string;
  icon: IconName;
  segments: string[];
  landing: string;
}

/** Five primary sections, each claiming every route that belongs to it. */
export const PROJECT_SECTIONS: ProjectSection[] = [
  { key: 'overview', labelKey: 'nav.overview', icon: 'home', segments: ['overview'], landing: 'overview' },
  {
    key: 'design',
    labelKey: 'nav.design',
    icon: 'workflow',
    segments: ['stages', 'gate', 'inputs'],
    landing: 'overview',
  },
  {
    key: 'artifacts',
    labelKey: 'nav.artifactsGroup',
    icon: 'layers',
    segments: ['artifacts', 'issues', 'metadata', 'decisions', 'graph', 'api'],
    landing: 'artifacts',
  },
  {
    key: 'delivery',
    labelKey: 'nav.deliveryGroup',
    icon: 'rocket',
    segments: ['handoff', 'deployments', 'change-requests'],
    landing: 'handoff',
  },
  {
    key: 'manage',
    labelKey: 'nav.manageGroup',
    icon: 'users',
    segments: ['members', 'grants', 'exemptions', 'audit', 'settings'],
    landing: 'members',
  },
];

export const PROJECT_SUBTABS: Record<ProjectSectionKey, ProjectSubTab[]> = {
  overview: [],
  design: [
    { key: 'stage', labelKey: 'nav.stage', segments: ['stages', 'gate'], landing: 'overview' },
    { key: 'inputs', labelKey: 'nav.inputs', segments: ['inputs'], landing: 'inputs' },
  ],
  artifacts: [
    { key: 'artifacts', labelKey: 'nav.artifacts', segments: ['artifacts'], landing: 'artifacts' },
    { key: 'issues', labelKey: 'nav.issues', segments: ['issues'], landing: 'issues' },
    { key: 'metadata', labelKey: 'nav.metadata', segments: ['metadata'], landing: 'metadata' },
    { key: 'decisions', labelKey: 'nav.decisions', segments: ['decisions'], landing: 'decisions' },
    { key: 'graph', labelKey: 'nav.graph', segments: ['graph'], landing: 'graph' },
    { key: 'api', labelKey: 'nav.api', segments: ['api'], landing: 'api' },
  ],
  delivery: [
    { key: 'handoff', labelKey: 'nav.handoff', segments: ['handoff'], landing: 'handoff' },
    { key: 'deployments', labelKey: 'nav.deployments', segments: ['deployments'], landing: 'deployments' },
    { key: 'change-requests', labelKey: 'nav.changeRequests', segments: ['change-requests'], landing: 'change-requests' },
  ],
  manage: [
    { key: 'members', labelKey: 'nav.members', segments: ['members'], landing: 'members' },
    { key: 'grants', labelKey: 'nav.grants', segments: ['grants'], landing: 'grants' },
    { key: 'exemptions', labelKey: 'nav.exemptions', segments: ['exemptions'], landing: 'exemptions' },
    { key: 'audit', labelKey: 'nav.audit', segments: ['audit'], landing: 'audit' },
    { key: 'settings', labelKey: 'nav.projectSettings', segments: ['settings'], landing: 'settings' },
  ],
};

function projectRemainder(pathname: string, projectId: string): string | null {
  const base = `/projects/${projectId}/`;
  if (!pathname.startsWith(base)) return null;
  return pathname.slice(base.length).split('/')[0] ?? '';
}

/** Which primary section owns this address. Gate and stage belong to 设计. */
export function projectSectionForPath(pathname: string, projectId: string): ProjectSectionKey {
  const segment = projectRemainder(pathname, projectId);
  if (segment === null) return 'overview';
  const section = PROJECT_SECTIONS.find((entry) => entry.segments.includes(segment));
  return section?.key ?? 'overview';
}

export function projectSubTabForPath(pathname: string, projectId: string): string | null {
  const sectionKey = projectSectionForPath(pathname, projectId);
  const segment = projectRemainder(pathname, projectId);
  if (segment === null) return null;
  const tab = PROJECT_SUBTABS[sectionKey].find((entry) => entry.segments.includes(segment));
  return tab?.key ?? null;
}

export function projectSectionHref(section: ProjectSectionKey, projectId: string, currentStageId?: string): string {
  if (section === 'design') {
    return currentStageId
      ? `/projects/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(currentStageId)}`
      : `/projects/${encodeURIComponent(projectId)}/overview`;
  }
  const entry = PROJECT_SECTIONS.find((item) => item.key === section);
  return `/projects/${encodeURIComponent(projectId)}/${entry?.landing ?? 'overview'}`;
}

export function projectSubTabHref(
  section: ProjectSectionKey,
  tab: ProjectSubTab,
  projectId: string,
  currentStageId?: string,
): string {
  if (section === 'design' && tab.key === 'stage') {
    return projectSectionHref('design', projectId, currentStageId);
  }
  return `/projects/${encodeURIComponent(projectId)}/${tab.landing}`;
}
