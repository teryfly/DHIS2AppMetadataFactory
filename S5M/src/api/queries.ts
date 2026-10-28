import { computed, toValue, type MaybeRefOrGetter } from 'vue';
import { useInfiniteQuery, useQuery } from '@tanstack/vue-query';
import { apiGet } from '@/api/http';
import { qk } from '@/api/keys';
import { fetchAllowedActions, type AllowedActionsView } from '@/api/actions';
import { isNotGenerated } from '@/api/notGenerated';
import type * as T from '@/api/types';

// ---------------------------------------------------------------- projects & workflow
export const useProjects = (includeArchived: MaybeRefOrGetter<boolean | undefined> = false) =>
  useQuery({
    queryKey: computed(() => qk.projects(toValue(includeArchived))),
    queryFn: () => apiGet<T.ProjectPage>('/projects', { query: { includeArchived: toValue(includeArchived) } }),
  });

export const useProject = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.project(toValue(id))),
    queryFn: () => apiGet<T.ProjectView>(`/projects/${encodeURIComponent(toValue(id))}`),
    enabled: computed(() => !!toValue(id)),
  });

export const useWorkflowOfProject = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.workflowOfProject(toValue(id))),
    queryFn: () => apiGet<T.WorkflowView>(`/projects/${encodeURIComponent(toValue(id))}/workflow`),
    enabled: computed(() => !!toValue(id)),
  });

export const useWorkflow = (wfId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.workflow(toValue(wfId))),
    queryFn: () => apiGet<T.WorkflowView>(`/workflows/${encodeURIComponent(toValue(wfId))}`),
    enabled: computed(() => !!toValue(wfId)),
  });

export const useStages = (wfId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.stages(toValue(wfId))),
    queryFn: () => apiGet<T.Page<T.StageView>>(`/workflows/${encodeURIComponent(toValue(wfId))}/stages`),
    enabled: computed(() => !!toValue(wfId)),
  });

export const useStage = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.stage(toValue(id))),
    queryFn: () => apiGet<T.StageDetailView>(`/stages/${encodeURIComponent(toValue(id))}`),
    enabled: computed(() => !!toValue(id)),
  });

/** The run S1 assembled from S3's result, its own question rows and the progress frames. */
export const useAgentRun = (
  wfId: MaybeRefOrGetter<string>,
  runId: MaybeRefOrGetter<string>,
  live: MaybeRefOrGetter<boolean> = false,
) =>
  useQuery({
    queryKey: computed(() => qk.agentRun(toValue(wfId), toValue(runId))),
    queryFn: () => apiGet<T.AgentRunView>(`/workflows/${encodeURIComponent(toValue(wfId))}/agent-runs/${encodeURIComponent(toValue(runId))}`),
    enabled: computed(() => !!toValue(wfId) && !!toValue(runId)),
    refetchInterval: computed(() => (toValue(live) ? 2_000 : false)),
  });

// ---------------------------------------------------------------- allowed-actions
export const useAllowedActions = (wfId: MaybeRefOrGetter<string>) =>
  useQuery<AllowedActionsView>({
    queryKey: computed(() => qk.allowedActions(toValue(wfId))),
    queryFn: () => fetchAllowedActions(toValue(wfId)),
    enabled: computed(() => !!toValue(wfId)),
  });

export const usePlatformActions = () =>
  useQuery<AllowedActionsView>({
    queryKey: qk.platformActions,
    queryFn: () => fetchAllowedActions(),
  });

// ---------------------------------------------------------------- questions
export const useQuestions = (wfId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.questions(toValue(wfId))),
    queryFn: () => apiGet<T.Page<T.QuestionView>>(`/workflows/${encodeURIComponent(toValue(wfId))}/questions`),
    enabled: computed(() => !!toValue(wfId)),
  });

export const useQuestion = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.question(toValue(id))),
    queryFn: () => apiGet<T.QuestionView>(`/questions/${encodeURIComponent(toValue(id))}`),
    enabled: computed(() => !!toValue(id)),
  });

/**
 * UM-HOME-002: the to-do centre merges four categories, and open questions are one of them.
 * S1's cross-project read exists for exactly this; asking once avoids a per-workflow scan that
 * would also have to guess which page of each workflow held the unanswered rows.
 */
export const useOpenQuestions = () =>
  useQuery({
    queryKey: qk.openQuestions,
    queryFn: () => apiGet<T.Page<T.QuestionView>>('/questions', { query: { status: 'OPEN', limit: 200 } }),
  });

// ---------------------------------------------------------------- gates
export const useGates = (status: MaybeRefOrGetter<string | undefined>) =>
  useQuery({
    queryKey: computed(() => qk.gates(toValue(status))),
    queryFn: () => apiGet<T.GateSummaryList>('/gates', { query: { status: toValue(status) } }),
  });

export const useGatesPending = () => useGates('WAITING');

export const useGate = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.gate(toValue(id))),
    queryFn: () => apiGet<T.GateView>(`/gates/${encodeURIComponent(toValue(id))}`),
    enabled: computed(() => !!toValue(id)),
  });

// ---------------------------------------------------------------- notifications
/**
 * The notification feed. `mine` and `unreadOnly` are S1's filters (CP-NTF-001), not local ones: the
 * feed is paged, so a filter applied here would mean "among the newest page" and the badge would
 * count something other than what the screen offers.
 */
/**
 * S1's own page size (S5IF-G04). Asking for more than this is the client deciding the server's
 * paging policy for it; the next page arrives through the cursor or the offset S1 handed back.
 */
export const PAGE_SIZE = 50;

/** How many rows the pages already fetched hold — the offset the next page starts at. */
function seenSoFar(pages: { items?: unknown[] }[]): number {
  return pages.reduce((n, p) => n + (p.items?.length ?? 0), 0);
}

/**
 * The audit trail, walked by cursor. `cursor` is opaque in the contract, so nothing here parses it:
 * whatever S1 handed back is what the next request carries. The filters go to S1 and the cursor
 * walks *that* filtered result, which is the only way "what did this operator change last week" is
 * answerable at all — the newest 50 rows are not that question.
 */
export const useAuditFeed = (
  projectId: MaybeRefOrGetter<string | undefined>,
  f: MaybeRefOrGetter<Record<string, string | number | undefined> | undefined> = undefined,
) =>
  useInfiniteQuery({
    queryKey: computed(() => qk.audit(toValue(projectId), toValue(f))),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => {
      const query = { ...toValue(f), limit: PAGE_SIZE, ...(pageParam ? { cursor: pageParam } : {}) };
      return toValue(projectId)
        ? apiGet<T.AuditPage>(`/projects/${encodeURIComponent(toValue(projectId)!)}/audit`, { query })
        : apiGet<T.AuditPage>('/audit', { query });
    },
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });

/** The notification feed, walked by offset, with S1's `mine`/`unreadOnly` filters (CP-NTF-001). */
export const useNotificationFeed = (
  filters: MaybeRefOrGetter<{ mine?: boolean; unreadOnly?: boolean } | undefined> = undefined,
) =>
  useInfiniteQuery({
    queryKey: computed(() => qk.notifications({ ...toValue(filters) })),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiGet<T.NotificationPage>('/notifications', { query: { ...toValue(filters), limit: PAGE_SIZE, offset: pageParam } }),
    getNextPageParam: (last, pages) => (seenSoFar(pages) < Number(last.total ?? 0) ? seenSoFar(pages) : undefined),
  });

/** The account directory, walked by offset (S5IF-G04) so the 101st account is reachable. */
export const useUserFeed = () =>
  useInfiniteQuery({
    queryKey: ['users', 'feed', PAGE_SIZE] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam }) => apiGet<T.UserList>('/users', { query: { limit: PAGE_SIZE, offset: pageParam } }),
    getNextPageParam: (last, pages) => (seenSoFar(pages) < Number(last.total ?? 0) ? seenSoFar(pages) : undefined),
  });

export const useNotifications = (
  projectId?: MaybeRefOrGetter<string | undefined>,
  filters?: MaybeRefOrGetter<{ mine?: boolean; unreadOnly?: boolean; limit?: number; offset?: number } | undefined>,
) =>
  useQuery({
    queryKey: computed(() => qk.notifications({ projectId: toValue(projectId), ...(toValue(filters) ?? {}) })),
    queryFn: () => apiGet<T.NotificationPage>('/notifications', {
      query: { projectId: toValue(projectId), ...(toValue(filters) ?? {}) },
    }),
  });

// ---------------------------------------------------------------- inputs / requirements
export const useUploadLimits = () =>
  useQuery({ queryKey: qk.uploadLimits, queryFn: () => apiGet<T.UploadLimitsView>('/upload-limits'), staleTime: 600_000 });

export const useInputs = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.inputs(toValue(projectId))),
    queryFn: () => apiGet<T.InputPage>(`/projects/${encodeURIComponent(toValue(projectId))}/inputs`),
    enabled: computed(() => !!toValue(projectId)),
    // A claimed parse is the only state that changes while the list sits open; the SSE
    // `input.parse` terminal event ends the poll earlier.
    refetchInterval: (query) => {
      const data = query.state.data as T.InputPage | undefined;
      return (data?.items ?? []).some((item) => item.parseStatus === 'PARSING') ? 3000 : false;
    },
  });

export const useColumnMapping = (projectId: MaybeRefOrGetter<string>, artifactId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.columnMapping(toValue(projectId), toValue(artifactId))),
    queryFn: () =>
      apiGet<T.ColumnMappingView>(
        `/projects/${encodeURIComponent(toValue(projectId))}/inputs/${encodeURIComponent(toValue(artifactId))}/column-mapping`,
      ),
    enabled: computed(() => !!toValue(projectId) && !!toValue(artifactId)),
  });

/**
 * S5IF-INP08: the model calls one input made. Polling is the progress signal while an attempt
 * has not returned; `input.parse` over SSE stops it as soon as the parse finishes.
 */
export const useLlmLog = (projectId: MaybeRefOrGetter<string>, artifactId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.llmLog(toValue(projectId), toValue(artifactId))),
    queryFn: () =>
      apiGet<T.LlmLogView>(
        `/projects/${encodeURIComponent(toValue(projectId))}/inputs/${encodeURIComponent(toValue(artifactId))}/llm-log`,
      ),
    enabled: computed(() => !!toValue(projectId) && !!toValue(artifactId)),
    refetchInterval: (query) => {
      const data = query.state.data as T.LlmLogView | undefined;
      const busy =
        data?.parseStatus === 'PARSING' ||
        (data?.items ?? []).some((item) => item.status === 'IN_FLIGHT');
      return busy ? 3000 : false;
    },
  });

/** S5IF-INP09: one attempt's request messages and raw answer, fetched when expanded. */
export const useLlmExchange = (
  projectId: MaybeRefOrGetter<string>,
  artifactId: MaybeRefOrGetter<string>,
  exchangeId: MaybeRefOrGetter<string | null>,
) =>
  useQuery({
    queryKey: computed(() =>
      qk.llmExchange(toValue(projectId), toValue(artifactId), toValue(exchangeId) ?? '-'),
    ),
    queryFn: () =>
      apiGet<T.LlmExchangeDetailView>(
        `/projects/${encodeURIComponent(toValue(projectId))}/inputs/${encodeURIComponent(toValue(artifactId))}/llm-log/${encodeURIComponent(toValue(exchangeId) ?? '')}`,
      ),
    enabled: computed(
      () => !!toValue(projectId) && !!toValue(artifactId) && !!toValue(exchangeId),
    ),
    refetchInterval: (query) => (query.state.data?.status === 'IN_FLIGHT' ? 3000 : false),
  });

export const useNrr = (
  workflowId: MaybeRefOrGetter<string>,
  f: MaybeRefOrGetter<{ category?: string; minConfidence?: number } | undefined> = undefined,
) =>
  useQuery({
    queryKey: computed(() => qk.nrr(toValue(workflowId), toValue(f))),
    queryFn: () => apiGet<T.NrrPage>(`/workflows/${encodeURIComponent(toValue(workflowId))}/nrr`, { query: toValue(f) }),
    enabled: computed(() => !!toValue(workflowId)),
  });

// ---------------------------------------------------------------- blueprint / registry
// A project whose first design stage has not run has no draft to read, and S1 says so with 404
// NOT_GENERATED (S5IF-BP05, CP-UI-022). Resolving that to `undefined` keeps the screens that
// already render with no draft out of the error toast; a genuine NOT_FOUND still throws.
export const useBlueprintDraft = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.blueprintDraft(toValue(projectId))),
    queryFn: async () => {
      try {
        return await apiGet<Record<string, unknown>>(`/projects/${encodeURIComponent(toValue(projectId))}/blueprint/draft`);
      } catch (error) {
        // `undefined` is a failed read to vue-query; `null` is an answer that says nothing yet
        if (isNotGenerated(error)) return null;
        throw error;
      }
    },
    enabled: computed(() => !!toValue(projectId)),
  });

/**
 * S5IF-BP04 — the section entries a blueprint screen actually needs.
 *
 * This is the read that replaces `blueprint/draft` for anything that wants entry content: S1 pins
 * that view's `document` to null on purpose (`services/blueprint_reads.py`, S5IF-UP03), so a screen
 * seeded from it silently starts every form empty. A project whose first design stage has not run
 * answers 404 NOT_GENERATED (CP-UI-022), which resolves to `null` here for the same reason the
 * draft read does; every other failure still throws.
 */
export const useBlueprintEntries = (
  projectId: MaybeRefOrGetter<string>,
  f: MaybeRefOrGetter<{ sections?: string[]; source?: 'DRAFT' | 'APPROVED' } | undefined> = undefined,
) =>
  useQuery({
    queryKey: computed(() => qk.blueprintEntries(toValue(projectId), toValue(f)?.sections)),
    queryFn: async () => {
      try {
        return await apiGet<T.EntryPage>(
          `/projects/${encodeURIComponent(toValue(projectId))}/blueprint/entries`,
          { query: toValue(f) },
        );
      } catch (error) {
        if (isNotGenerated(error)) return null;
        throw error;
      }
    },
    enabled: computed(() => !!toValue(projectId)),
  });

export const useSectionSchemas = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.sectionSchemas(toValue(projectId))),
    queryFn: () => apiGet<T.SectionSchemaIndexView>(`/projects/${encodeURIComponent(toValue(projectId))}/blueprint/section-schemas`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useSectionSchema = (projectId: MaybeRefOrGetter<string>, section: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.sectionSchema(toValue(projectId), toValue(section))),
    queryFn: () =>
      apiGet<T.SectionSchemaView>(
        `/projects/${encodeURIComponent(toValue(projectId))}/blueprint/section-schemas/${encodeURIComponent(toValue(section))}`,
      ),
    enabled: computed(() => !!toValue(projectId) && !!toValue(section)),
  });

export const useBlueprintVersions = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.blueprintVersions(toValue(projectId))),
    queryFn: () => apiGet<T.Page<Record<string, unknown>>>(`/projects/${encodeURIComponent(toValue(projectId))}/blueprint/versions`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useRegistry = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.registry(toValue(projectId))),
    queryFn: () => apiGet<T.RegistryPage>(`/projects/${encodeURIComponent(toValue(projectId))}/registry`),
    enabled: computed(() => !!toValue(projectId)),
  });

// ---------------------------------------------------------------- artifacts
export const useArtifacts = (
  wfId: MaybeRefOrGetter<string>,
  f: MaybeRefOrGetter<{ type?: string; limit?: number } | undefined> = undefined,
) =>
  useQuery({
    queryKey: computed(() => qk.workflowArtifacts(toValue(wfId), toValue(f))),
    queryFn: () => apiGet<T.ArtifactPage>(`/workflows/${encodeURIComponent(toValue(wfId))}/artifacts`, { query: toValue(f) }),
    enabled: computed(() => !!toValue(wfId)),
  });

export const useArtifact = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.artifact(toValue(id))),
    queryFn: () => apiGet<T.ArtifactView>(`/artifacts/${encodeURIComponent(toValue(id))}`),
    enabled: computed(() => !!toValue(id)),
  });

/**
 * The artifact's bytes, read as text.
 *
 * `enabled` exists because a screen that has already decided it cannot render the file should not
 * pull 20 MB of .docx through `res.text()` to discover that — the caller passes the media-type check
 * (`lib/media.ts`) and the page shows S1's download link instead.
 */
export const useArtifactContent = (id: MaybeRefOrGetter<string>, enabled: MaybeRefOrGetter<boolean> = true) =>
  useQuery({
    queryKey: computed(() => qk.artifactContent(toValue(id))),
    queryFn: () => apiGet<string>(`/artifacts/${encodeURIComponent(toValue(id))}/content`, { parseAs: 'text' }),
    enabled: computed(() => !!toValue(id) && toValue(enabled)),
  });

export const useArtifactLineage = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.artifactLineage(toValue(id))),
    queryFn: () => apiGet<T.Lineage>(`/artifacts/${encodeURIComponent(toValue(id))}/lineage`),
    enabled: computed(() => !!toValue(id)),
  });

export const useValidationIssues = (
  wfId: MaybeRefOrGetter<string>,
  f: MaybeRefOrGetter<{ level?: string; sourceStage?: string; ruleId?: string; businessKey?: string } | undefined> = undefined,
) =>
  useQuery({
    queryKey: computed(() => qk.validationIssues(toValue(wfId), toValue(f))),
    queryFn: () => apiGet<T.ValidationIssuePage>(`/workflows/${encodeURIComponent(toValue(wfId))}/validation-issues`, { query: toValue(f) }),
    enabled: computed(() => !!toValue(wfId)),
  });

// ---------------------------------------------------------------- deployments
export const useRecentRuns = () =>
  useQuery({ queryKey: qk.recentRuns, queryFn: () => apiGet<T.RunPage>('/deployment-runs') });

export const useDeploymentRuns = (wfId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.deploymentRuns(toValue(wfId))),
    queryFn: () => apiGet<T.Page<T.RunView>>(`/workflows/${encodeURIComponent(toValue(wfId))}/deployment-runs`),
    enabled: computed(() => !!toValue(wfId)),
  });

export const useRun = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.run(toValue(id))),
    queryFn: () => apiGet<T.RunDetailView>(`/deployment-runs/${encodeURIComponent(toValue(id))}`),
    enabled: computed(() => !!toValue(id)),
  });

export const useRunSummary = (id: MaybeRefOrGetter<string>, active?: MaybeRefOrGetter<boolean>) =>
  useQuery({
    queryKey: computed(() => qk.runSummary(toValue(id))),
    queryFn: () => apiGet<T.RunSummaryView>(`/deployment-runs/${encodeURIComponent(toValue(id))}/summary`),
    enabled: computed(() => !!toValue(id)),
    // SSE is the primary channel; this is the fallback for a screen whose stream dropped (UM-NET-004).
    refetchInterval: computed(() => (toValue(active ?? false) ? 5000 : false)),
  });

export const useRunBindings = (id: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.runBindings(toValue(id))),
    queryFn: () => apiGet<T.BindingPage>(`/deployment-runs/${encodeURIComponent(toValue(id))}/environment-bindings`),
    enabled: computed(() => !!toValue(id)),
  });

// ---------------------------------------------------------------- environments
/** `enabled` lets a consumer that only needs the list on demand (the shell's search panel) leave it
 *  alone until it is asked for, instead of putting an environment read on every cold start. */
export const useEnvironments = (enabled: MaybeRefOrGetter<boolean> = true) =>
  useQuery({ queryKey: qk.environments, queryFn: () => apiGet<T.EnvironmentPage>('/environments'), enabled: computed(() => toValue(enabled)) });

/**
 * The cold-start facts behind the "开始使用" checklist (S5IF-SET01). Facts only: whether a control
 * is usable keeps coming from `allowed-actions` (UM-BR-01), and a section S1 withholds arrives as
 * null rather than as a zero the page would render as "nothing registered".
 */
export const useSetup = () => useQuery({ queryKey: qk.setup, queryFn: () => apiGet<T.SetupView>('/setup') });

/**
 * Which credentials have already been entered (S5IF-SEC02): names, references and timestamps.
 * S1 never returns a value, a mask or a length (S5IF-G09), and this list is what lets a second
 * environment reuse a token instead of making the operator type it again.
 */
export const useSecrets = () => useQuery({ queryKey: qk.secrets, queryFn: () => apiGet<T.SecretListView>('/secrets') });

/**
 * Built-in vendor addresses and model names (S5IF-GOV07). A suggestion list: it carries no
 * reachability field, so no screen can draw a green light from it — only the connection test
 * (S5IF-ENV05) and a real stage run can say whether something answers.
 */
export const useProviderCatalogue = () =>
  useQuery({
    queryKey: qk.providerCatalogue,
    queryFn: () => apiGet<T.ProviderCatalogueView>('/governance/provider-catalogue'),
  });


export const useEnvironment = (key: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.environment(toValue(key))),
    queryFn: () => apiGet<T.EnvironmentView>(`/environments/${encodeURIComponent(toValue(key))}`),
    enabled: computed(() => !!toValue(key)),
  });

export const useCapabilityProfile = (key: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.capabilityProfile(toValue(key))),
    queryFn: () => apiGet<Record<string, unknown>>(`/environments/${encodeURIComponent(toValue(key))}/capability-profile`),
    enabled: computed(() => !!toValue(key)),
  });

export const useExpiringCredentials = () =>
  useQuery({ queryKey: qk.expiringCredentials, queryFn: () => apiGet<T.ExpiringCredentialList>('/environments/expiring-credentials') });

// ---------------------------------------------------------------- governance
export const useGovernanceModels = () =>
  useQuery({ queryKey: qk.governanceModels, queryFn: () => apiGet<T.ModelList>('/governance/models') });

export const useGovernanceProviders = () =>
  useQuery({ queryKey: qk.governanceProviders, queryFn: () => apiGet<T.ProviderList>('/governance/providers') });

export const useGovernancePolicy = () =>
  useQuery({ queryKey: qk.governancePolicy, queryFn: () => apiGet<T.PolicyView>('/governance/policy') });

export const useGovernancePrompts = () =>
  useQuery({ queryKey: qk.governancePrompts, queryFn: () => apiGet<Record<string, unknown>[]>('/governance/prompts') });

export const useGovernanceUsage = () =>
  useQuery({ queryKey: qk.governanceUsage, queryFn: () => apiGet<T.UsageView>('/governance/usage') });

// ---------------------------------------------------------------- settings / CR / audit
export const useSystemSettings = () =>
  useQuery({ queryKey: qk.settings, queryFn: () => apiGet<T.SystemSettingsView>('/settings') });

export const useChangeRequests = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.changeRequests(toValue(projectId))),
    queryFn: () => apiGet<T.ChangeRequestList>(`/projects/${encodeURIComponent(toValue(projectId))}/change-requests`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useCommitTrace = (executionId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.commitTrace(toValue(executionId))),
    queryFn: () => apiGet<T.CommitTraceView>(`/audit/commit-trace/${encodeURIComponent(toValue(executionId))}`),
    enabled: computed(() => !!toValue(executionId)),
  });

// ---------------------------------------------------------------- M2/M3/M4 additions
export const useBlueprintDiff = (projectId: MaybeRefOrGetter<string>, from: MaybeRefOrGetter<string>, to: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'blueprint', 'diff', toValue(from), toValue(to)]),
    queryFn: () => apiGet<T.BlueprintDiffView>(`/projects/${encodeURIComponent(toValue(projectId))}/blueprint/diff`, { query: { fromVersion: toValue(from), toVersion: toValue(to) } }),
    enabled: computed(() => !!toValue(projectId) && !!toValue(from) && !!toValue(to)),
  });

export const useArtifactDiff = (artifactId: MaybeRefOrGetter<string>, against: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['artifacts', toValue(artifactId), 'diff', toValue(against)]),
    queryFn: () => apiGet<T.ArtifactDiffView>(`/artifacts/${encodeURIComponent(toValue(artifactId))}/diff`, { query: { against: toValue(against) } }),
    enabled: computed(() => !!toValue(artifactId) && !!toValue(against)),
  });

export const useExecution = (executionId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['executions', toValue(executionId)]),
    queryFn: () => apiGet<T.ExecutionView>(`/executions/${encodeURIComponent(toValue(executionId))}`),
    enabled: computed(() => !!toValue(executionId)),
  });

export const useExecutionReport = (executionId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['executions', toValue(executionId), 'report']),
    queryFn: () => apiGet<T.ExecutionReportView>(`/executions/${encodeURIComponent(toValue(executionId))}/report`),
    enabled: computed(() => !!toValue(executionId)),
  });

export const useBindingCandidates = (runId: MaybeRefOrGetter<string>, businessKey: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['deployment-runs', toValue(runId), 'candidates', toValue(businessKey)]),
    queryFn: () =>
      apiGet<T.CandidatePage>(
        `/deployment-runs/${encodeURIComponent(toValue(runId))}/environment-bindings/${encodeURIComponent(toValue(businessKey))}/candidates`,
      ),
    enabled: computed(() => !!toValue(runId) && !!toValue(businessKey)),
  });

export const useGateExplanation = (gateId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['gates', toValue(gateId), 'explanation']),
    queryFn: () => apiGet<T.GateExplanationView>(`/gates/${encodeURIComponent(toValue(gateId))}/explanation`),
    enabled: computed(() => !!toValue(gateId)),
  });

export const useUidPool = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'uid-pool']),
    queryFn: () => apiGet<T.UidPoolView>(`/projects/${encodeURIComponent(toValue(projectId))}/uid-pool`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useManagedScope = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'managed-scope']),
    queryFn: () => apiGet<T.ManagedScopePage>(`/projects/${encodeURIComponent(toValue(projectId))}/registry/managed-scope`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useDecisions = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'decisions']),
    queryFn: () => apiGet<T.DecisionList>(`/projects/${encodeURIComponent(toValue(projectId))}/decisions`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useEffectivePolicy = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'effective-policy']),
    queryFn: () => apiGet<T.ProjectPolicyView>(`/projects/${encodeURIComponent(toValue(projectId))}/effective-policy`),
    enabled: computed(() => !!toValue(projectId)),
  });

export const useEnvironmentProtection = (key: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['environments', toValue(key), 'protection']),
    queryFn: () => apiGet<T.EnvironmentProtectionView>(`/environments/${encodeURIComponent(toValue(key))}/protection`),
    enabled: computed(() => !!toValue(key)),
  });

export const useGovernanceEvaluations = () =>
  useQuery({ queryKey: ['governance', 'evaluations'], queryFn: () => apiGet<T.EvaluationList>('/governance/evaluations') });

export const useGovernanceBudget = (projectId?: MaybeRefOrGetter<string | undefined>) =>
  useQuery({
    queryKey: computed(() => ['governance', 'budget', toValue(projectId) ?? null]),
    queryFn: () => apiGet<T.BudgetView>('/governance/budget', { query: { projectId: toValue(projectId) } }),
  });

// -------------------------------------------------------------------- M4 auth & permissions
export const useUsers = (page?: MaybeRefOrGetter<{ limit?: number; offset?: number } | undefined>) =>
  useQuery({
    queryKey: computed(() => ['users', toValue(page) ?? null]),
    queryFn: () => apiGet<T.UserList>('/users', { query: toValue(page) }),
  });

export const useRoles = () =>
  useQuery({ queryKey: ['roles'], queryFn: () => apiGet<T.RoleList>('/roles'), staleTime: 300_000 });

/**
 * FR-SEC-004: who belongs to this project, and in what role. S1 answers the whole set and the write
 * is a PUT of that set, so this read is the draft the members screen edits (see `useReplaceMembers`).
 */
export const useProjectMembers = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.members(toValue(projectId))),
    queryFn: () => apiGet<T.MemberList>(`/projects/${encodeURIComponent(toValue(projectId))}/members`),
    enabled: computed(() => !!toValue(projectId)),
  });

/**
 * FR-SEC-003: the standing authorisations to read PHI_PII / RESTRICTED artifacts. The read itself
 * asks `restricted_grant.manage` of S1, so an operator without the right gets an error rather than
 * an empty list, and the screen says so instead of concluding "nobody is authorised".
 */
export const useArtifactGrants = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.artifactGrants(toValue(projectId))),
    queryFn: () => apiGet<T.GrantList>(`/projects/${encodeURIComponent(toValue(projectId))}/artifact-grants`),
    enabled: computed(() => !!toValue(projectId)),
  });

/** CP-WF-020: the recorded production waivers for this project, revoked ones included. */
export const useProductionExemptions = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.productionExemptions(toValue(projectId))),
    queryFn: () => apiGet<T.ExemptionList>(`/projects/${encodeURIComponent(toValue(projectId))}/production-exemptions`),
    enabled: computed(() => !!toValue(projectId)),
  });

// -------------------------------------------------------------------- M3 package / deliverables
export const usePackageFiles = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'package-files']),
    queryFn: () => apiGet<T.PackageFileList>(`/projects/${encodeURIComponent(toValue(projectId))}/package-files`),
    enabled: computed(() => !!toValue(projectId)),
  });

/**
 * CP-UI-021: the mode-B development package — S2's own layout, forwarded whole. It is a different
 * thing from the build package above: that one is what goes to an environment, this one is what a
 * frontend team works from. "No package yet" is a 404 with `reason=NOT_GENERATED` (CP-UI-022), and
 * `readIntentOf` turns it into guidance.
 */
export const useFrontendPackage = (projectId: MaybeRefOrGetter<string>, enabled: MaybeRefOrGetter<boolean> = true) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'frontend-package']),
    queryFn: () => apiGet<T.FrontendPackageFilesView>(`/projects/${encodeURIComponent(toValue(projectId))}/frontend-package/files`),
    enabled: computed(() => !!toValue(projectId) && toValue(enabled)),
  });

/** One file of that package, as text. The download link beside it is the browser's own job. */
export const useFrontendPackageFile = (projectId: MaybeRefOrGetter<string>, path: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['projects', toValue(projectId), 'frontend-package', toValue(path)]),
    queryFn: () => apiGet<string>(`/projects/${encodeURIComponent(toValue(projectId))}/frontend-package/files/${toValue(path)}`, { parseAs: 'text' }),
    enabled: computed(() => !!toValue(projectId) && !!toValue(path)),
  });

// -------------------------------------------------------------------- M3 stage chat
export const useChat = (stageId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => ['stages', toValue(stageId), 'chat']),
    queryFn: () => apiGet<T.ChatTranscriptView>(`/stages/${encodeURIComponent(toValue(stageId))}/chat`),
    enabled: computed(() => !!toValue(stageId)),
  });

export const useGovernanceAgents = () =>
  useQuery({ queryKey: ['governance', 'agents'], queryFn: () => apiGet<T.AgentListView>('/governance/agents') });

/** UI-PRJ-004: the project's own reminder and notification settings, read before they are edited. */
export const useProjectSettings = (projectId: MaybeRefOrGetter<string>) =>
  useQuery({
    queryKey: computed(() => qk.projectSettings(toValue(projectId))),
    queryFn: () => apiGet<T.ProjectSettingsBody>(`/projects/${encodeURIComponent(toValue(projectId))}/settings`),
    enabled: computed(() => !!toValue(projectId)),
  });

/** UI-ADM-003 / AI-REG-003: which prompt versions already carry a Golden Project regression. */
export const useRegressions = () =>
  useQuery({ queryKey: ['governance', 'regression-records'], queryFn: () => apiGet<T.RegressionList>('/governance/regression-records') });
