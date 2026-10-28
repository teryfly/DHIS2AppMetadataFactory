/*
 * Central query-key registry. Keys are hierarchical so an SSE event or a 409 can invalidate the
 * exact resources affected (UI-RT-002: events only trigger refetch, never carry state).
 */
export const qk = {
  health: ['health'] as const,
  setup: ['setup'] as const,
  secrets: ['secrets'] as const,
  providerCatalogue: ['governance', 'provider-catalogue'] as const,
  agents: ['governance', 'agents'] as const,

  projects: ['projects'] as const,
  projectList: (filters?: Record<string, string | boolean | number | undefined>) =>
    ['projects', 'list', filters ?? {}] as const,
  project: (projectId: string) => ['projects', projectId] as const,
  projectSettings: (projectId: string) => ['projects', projectId, 'settings'] as const,
  targetEnvironments: (projectId: string) => ['projects', projectId, 'target-environments'] as const,
  uidPool: (projectId: string) => ['projects', projectId, 'uid-pool'] as const,
  workflow: (projectId: string) => ['projects', projectId, 'workflow'] as const,
  stages: (workflowId: string) => ['workflows', workflowId, 'stages'] as const,
  stage: (stageId: string) => ['stages', stageId] as const,
  allowedActions: (workflowId: string) => ['workflows', workflowId, 'allowed-actions'] as const,
  platformAllowedActions: ['allowed-actions', 'platform'] as const,
  workflowArtifacts: (workflowId: string, filters?: Record<string, string | undefined>) =>
    ['workflows', workflowId, 'artifacts', filters ?? {}] as const,
  artifact: (artifactId: string) => ['artifacts', artifactId] as const,
  artifactContent: (artifactId: string) => ['artifacts', artifactId, 'content'] as const,
  artifactDiff: (artifactId: string, against: string) => ['artifacts', artifactId, 'diff', against] as const,
  artifactLineage: (artifactId: string) => ['artifacts', artifactId, 'lineage'] as const,
  questions: (workflowId: string) => ['workflows', workflowId, 'questions'] as const,
  question: (questionId: string) => ['questions', questionId] as const,
  gates: (workflowId: string) => ['workflows', workflowId, 'gates'] as const,
  gate: (gateId: string) => ['gates', gateId] as const,
  // UI-GATE-001: the page's six questions read this; it lives under the gate detail key so a
  // gate.* invalidation (approve/reject/invalidate) reaches it too.
  gateExplanation: (gateId: string) => ['gates', gateId, 'explanation'] as const,
  nrr: (workflowId: string, filters?: Record<string, string | number | undefined>) =>
    ['workflows', workflowId, 'nrr', filters ?? {}] as const,
  validationIssues: (workflowId: string, filters?: Record<string, string | undefined>) =>
    ['workflows', workflowId, 'validation-issues', filters ?? {}] as const,
  agentRun: (workflowId: string, runId: string) => ['workflows', workflowId, 'agent-runs', runId] as const,

  inputs: (projectId: string) => ['projects', projectId, 'inputs'] as const,
  columnMapping: (projectId: string, artifactId: string) =>
    ['projects', projectId, 'inputs', artifactId, 'column-mapping'] as const,
  llmLog: (projectId: string, artifactId: string) =>
    ['projects', projectId, 'inputs', artifactId, 'llm-log'] as const,
  llmExchange: (projectId: string, artifactId: string, exchangeId: string) =>
    ['projects', projectId, 'inputs', artifactId, 'llm-log', exchangeId] as const,
  registry: (projectId: string) => ['projects', projectId, 'registry'] as const,
  packageFiles: (projectId: string, version = '', environmentKey = '') =>
    ['projects', projectId, 'package-files', version, environmentKey] as const,
  // CP-UI-021: the mode-B development package is a different thing from the build package above —
  // S2's own layout, forwarded whole, and the deliverable a frontend team works from.
  frontendPackage: (projectId: string) => ['projects', projectId, 'frontend-package'] as const,
  frontendPackageFile: (projectId: string, path: string) =>
    ['projects', projectId, 'frontend-package', path] as const,
  // every filter is part of the key: a graph that refetched on nothing but the project id would
  // keep showing the previous type filter's picture under the new filter's label. The read is
  // workflow-scoped (S1 deprecated the project path), but the key stays under the project so the
  // screen's invalidation root is unchanged.
  objectGraph: (
    projectId: string,
    workflowId = '',
    runId = '',
    blueprintVersion = '',
    businessKeys: string[] = [],
    dhis2Types: string[] = [],
  ) =>
    [
      'projects',
      projectId,
      'object-graph',
      workflowId,
      runId,
      blueprintVersion,
      [...businessKeys].sort().join(','),
      [...dhis2Types].sort().join(','),
    ] as const,
  blueprintDraft: (projectId: string) => ['projects', projectId, 'blueprint', 'draft'] as const,
  blueprintVersions: (projectId: string) => ['projects', projectId, 'blueprint', 'versions'] as const,
  blueprintDiff: (projectId: string, from: string, to: string) =>
    ['projects', projectId, 'blueprint', 'diff', from, to] as const,
  blueprintEntries: (projectId: string, sections?: string[], source = 'APPROVED') =>
    ['projects', projectId, 'blueprint', 'entries', source, (sections ?? []).join(',')] as const,
  sectionSchemas: (projectId: string) => ['projects', projectId, 'blueprint', 'section-schemas'] as const,
  // the ids asked for are part of the key: the Registry row that opened this list is a different
  // question from the whole project's decisions, and one cache must not answer the other
  decisions: (projectId: string, decisionIds: string[] = []) =>
    ['projects', projectId, 'decisions', [...decisionIds].sort().join(',')] as const,

  deploymentRuns: (workflowId: string) => ['workflows', workflowId, 'deployment-runs'] as const,
  runList: (filters?: Record<string, string | undefined>) => ['deployment-runs', 'list', filters ?? {}] as const,
  run: (runId: string) => ['deployment-runs', runId] as const,
  runSummary: (runId: string) => ['deployment-runs', runId, 'summary'] as const,
  bindings: (runId: string) => ['deployment-runs', runId, 'environment-bindings'] as const,
  execution: (executionId: string) => ['executions', executionId] as const,
  executionReport: (executionId: string) => ['executions', executionId, 'report'] as const,
  // the search text is part of the key: a candidate list that refetched on nothing but the
  // business key would keep showing the previous query's answers
  bindingCandidates: (runId: string, businessKey: string, query = '') =>
    ['deployment-runs', runId, 'environment-bindings', businessKey, 'candidates', query] as const,

  gatesPending: ['gates', 'pending'] as const,
  // the approver's inbox is the same endpoint with S1's own filters and page: a key per filter set, so
  // switching between "everybody" and "awaiting me" cannot leave the other one's rows on screen
  gatesInbox: (filters?: Record<string, string | undefined>) => ['gates', 'inbox', filters ?? {}] as const,

  environments: ['environments'] as const,
  environment: (key: string) => ['environments', key] as const,
  environmentProtection: (key: string) => ['environments', key, 'protection'] as const,
  capabilityProfile: (key: string) => ['environments', key, 'capability-profile'] as const,
  connectionTest: (key: string) => ['environments', key, 'connection-test'] as const,
  expiringCredentials: ['environments', 'expiring-credentials'] as const,

  governanceModels: ['governance', 'models'] as const,
  governanceProviders: ['governance', 'providers'] as const,
  governancePolicy: ['governance', 'policy'] as const,
  governancePrompts: ['governance', 'prompts'] as const,
  governanceEvaluations: ['governance', 'evaluations'] as const,
  governanceRegressions: ['governance', 'regression-records'] as const,
  // Both are per project and the rollup is per grouping, so the key carries what the request
  // carries (UI-APP-006): a shared key would make two views of the same budget overwrite each other.
  governanceUsage: (projectId: string, groupBy?: string[]) =>
    ['governance', 'usage', projectId, { groupBy: groupBy ?? [] }] as const,
  governanceBudget: (projectId: string) => ['governance', 'budget', projectId] as const,

  session: ['session'] as const,
  stageChat: (stageId: string) => ['stages', stageId, 'chat'] as const,
  users: ['users'] as const,
  roles: ['roles'] as const,
  members: (projectId: string) => ['projects', projectId, 'members'] as const,
  artifactGrants: (projectId: string) => ['projects', projectId, 'artifact-grants'] as const,
  productionExemptions: (projectId: string) => ['projects', projectId, 'production-exemptions'] as const,

  notifications: (filters?: Record<string, string | undefined>) => ['notifications', filters ?? {}] as const,
  systemSettings: ['settings'] as const,
  uploadLimits: ['upload-limits'] as const,

  audit: (scope: 'global' | string) => ['audit', scope] as const,

  changeRequests: (projectId: string) => ['projects', projectId, 'change-requests'] as const,
};

/** Invalidate everything for a project after `stream.reset` (UI-RT-003). */
export function projectRootKeys(projectId: string, workflowId?: string) {
  return [qk.project(projectId), qk.workflow(projectId), ...(workflowId ? [['workflows', workflowId] as const] : [])];
}
