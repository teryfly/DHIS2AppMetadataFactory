// Hierarchical query keys so mutations and SSE events can invalidate precisely by prefix.
// Server state lives only in TanStack Query; these keys are the single naming source (UM-P-01).
import type { QueryKey } from '@tanstack/vue-query';

export const qk = {
  all: ['s5m'] as const,
  setup: ['setup'] as const,
  secrets: ['secrets'] as const,
  providerCatalogue: ['governance', 'provider-catalogue'] as const,

  projects: (includeArchived?: boolean) => ['projects', { includeArchived: !!includeArchived }] as const,
  projectsList: ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  workflowOfProject: (id: string) => ['projects', id, 'workflow'] as const,

  workflow: (wfId: string) => ['workflows', wfId] as const,
  stages: (wfId: string) => ['workflows', wfId, 'stages'] as const,
  questions: (wfId: string) => ['workflows', wfId, 'questions'] as const,
  workflowGates: (wfId: string) => ['workflows', wfId, 'gates'] as const,
  workflowArtifacts: (wfId: string, f?: unknown) => ['workflows', wfId, 'artifacts', f ?? null] as const,
  nrr: (wfId: string, f?: unknown) => ['workflows', wfId, 'nrr', f ?? null] as const,
  validationIssues: (wfId: string, f?: unknown) => ['workflows', wfId, 'validation-issues', f ?? null] as const,
  allowedActions: (wfId: string) => ['workflows', wfId, 'allowed-actions'] as const,
  workflowGraph: (wfId: string, f?: unknown) => ['workflows', wfId, 'object-graph', f ?? null] as const,
  platformActions: ['allowed-actions', 'platform'] as const,

  stage: (id: string) => ['stages', id] as const,

  question: (id: string) => ['questions', id] as const,
  /** Every open question across projects — the to-do centre's fourth category (UM-HOME-002). */
  openQuestions: ['questions', 'open'] as const,
  agentRun: (wfId: string, runId: string) => ['workflows', wfId, 'agent-runs', runId] as const,

  gates: (status?: string) => ['gates', { status: status ?? null }] as const,
  gatesPending: ['gates'] as const,
  gate: (id: string) => ['gates', id] as const,

  notifications: (filters?: { projectId?: string; mine?: boolean; unreadOnly?: boolean; limit?: number; offset?: number }) =>
    ['notifications', {
      projectId: filters?.projectId ?? null,
      mine: filters?.mine ?? false,
      unreadOnly: filters?.unreadOnly ?? false,
      limit: filters?.limit ?? null,
      offset: filters?.offset ?? 0,
    }] as const,
  notificationsList: ['notifications'] as const,

  environments: ['environments'] as const,
  environment: (key: string) => ['environments', key] as const,
  capabilityProfile: (key: string) => ['environments', key, 'capability-profile'] as const,
  connectionTest: (key: string) => ['environments', key, 'test-connection'] as const,
  expiringCredentials: ['environments', 'expiring-credentials'] as const,

  governanceModels: ['governance', 'models'] as const,
  governanceProviders: ['governance', 'providers'] as const,
  governancePolicy: ['governance', 'policy'] as const,
  governancePrompts: ['governance', 'prompts'] as const,
  governanceUsage: ['governance', 'usage'] as const,

  settings: ['settings'] as const,
  uploadLimits: ['upload-limits'] as const,

  inputs: (projectId: string) => ['projects', projectId, 'inputs'] as const,
  columnMapping: (projectId: string, artifactId: string) => ['projects', projectId, 'inputs', artifactId, 'column-mapping'] as const,
  llmLog: (projectId: string, artifactId: string) => ['projects', projectId, 'inputs', artifactId, 'llm-log'] as const,
  llmExchange: (projectId: string, artifactId: string, exchangeId: string) =>
    ['projects', projectId, 'inputs', artifactId, 'llm-log', exchangeId] as const,
  projectSettings: (projectId: string) => ['projects', projectId, 'settings'] as const,
  targetEnvironments: (projectId: string) => ['projects', projectId, 'target-environments'] as const,
  // M4 (CP-UI-023): the three project-scoped authorization tables. Under the project on purpose —
  // `projectRootKeys` and `useAssignRole` already invalidate by that prefix, and a member row is a
  // project fact rather than a platform one.
  members: (projectId: string) => ['projects', projectId, 'members'] as const,
  artifactGrants: (projectId: string) => ['projects', projectId, 'artifact-grants'] as const,
  productionExemptions: (projectId: string) => ['projects', projectId, 'production-exemptions'] as const,
  blueprintDraft: (projectId: string) => ['projects', projectId, 'blueprint', 'draft'] as const,
  // S5IF-BP04. Keyed by the sections asked for, because the answer only ever holds those. The
  // no-argument form is the 4-element prefix a write invalidates: TanStack matches a *shorter*
  // filter key against a longer query key, so appending anything here — an empty joined string
  // included — would make the invalidation match nothing and the entry list would keep showing the
  // state from before the save.
  blueprintEntries: (projectId: string, sections?: readonly string[]) =>
    sections?.length
      ? (['projects', projectId, 'blueprint', 'entries', sections.join(',')] as const)
      : (['projects', projectId, 'blueprint', 'entries'] as const),
  blueprintVersions: (projectId: string) => ['projects', projectId, 'blueprint', 'versions'] as const,
  sectionSchemas: (projectId: string) => ['projects', projectId, 'blueprint', 'section-schemas'] as const,
  sectionSchema: (projectId: string, section: string) => ['projects', projectId, 'blueprint', 'section-schemas', section] as const,
  registry: (projectId: string) => ['projects', projectId, 'registry'] as const,

  artifact: (id: string) => ['artifacts', id] as const,
  artifactContent: (id: string) => ['artifacts', id, 'content'] as const,
  artifactDiff: (id: string, against?: string) => ['artifacts', id, 'diff', against ?? null] as const,
  artifactLineage: (id: string) => ['artifacts', id, 'lineage'] as const,

  recentRuns: ['deployment-runs'] as const,
  deploymentRuns: (wfId: string) => ['workflows', wfId, 'deployment-runs'] as const,
  run: (id: string) => ['deployment-runs', id] as const,
  runSummary: (id: string) => ['deployment-runs', id, 'summary'] as const,
  runBindings: (id: string) => ['deployment-runs', id, 'bindings'] as const,

  changeRequests: (projectId: string) => ['projects', projectId, 'change-requests'] as const,
  changeRequest: (id: string) => ['change-requests', id] as const,

  audit: (projectId?: string, f?: unknown) => ['audit', { projectId: projectId ?? null, f: f ?? null }] as const,
  commitTrace: (executionId: string) => ['audit', 'commit-trace', executionId] as const,
};

/** Keys under a project to reset when a project-wide refresh is needed (e.g. stream.reset). */
export function projectRootKeys(projectId: string, workflowId?: string): QueryKey[] {
  const roots: QueryKey[] = [qk.project(projectId), qk.inputs(projectId), qk.registry(projectId), qk.notifications({ projectId })];
  if (workflowId) roots.push(qk.workflow(workflowId), qk.stages(workflowId), qk.allowedActions(workflowId));
  return roots;
}
