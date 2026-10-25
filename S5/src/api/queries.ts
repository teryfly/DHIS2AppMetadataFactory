/*
 * Reusable read-model query options. Every server value is fetched from S1 through TanStack
 * Query (UI-APP-006); views never cache business state themselves. Responses are typed via the
 * generated OpenAPI client (UI-APP-007).
 */
import { keepPreviousData, infiniteQueryOptions, queryOptions } from '@tanstack/react-query';
import { client, unwrap } from './client';
import { isNotGenerated } from './errors';
import { qk } from './queryKeys';
import type * as T from './types';

const P = '/api/v1';

/**
 * S1's own default page size (S5IF-G04). Asking for more than this is the client deciding the
 * server's paging policy for it, and the cursor is how the next one arrives.
 */
export const PAGE_SIZE = 50;

/** A cursor-paged list, narrowed to what the trail actually carries. */
interface AuditPage {
  items?: T.AuditRecordView[];
  nextCursor?: string | null;
  total?: number | null;
}

export const projectsListOptions = (includeArchived = false) =>
  queryOptions({
    queryKey: qk.projectList({ includeArchived }),
    queryFn: () =>
      unwrap<T.ProjectPage>(() =>
        client.GET(`${P}/projects`, { params: { query: { includeArchived } } }),
      ),
  });

export const projectOptions = (projectId: string) =>
  queryOptions({
    queryKey: qk.project(projectId),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.ProjectView>(() =>
        client.GET(`${P}/projects/{project_id}`, { params: { path: { project_id: projectId } } }),
      ),
  });

export const workflowOptions = (projectId: string) =>
  queryOptions({
    queryKey: qk.workflow(projectId),
    enabled: !!projectId,
    queryFn: async () => {
      // Resolve the workflow id from the project (S1 keys workflows by id), without ever
      // persisting that id in a store — re-derived from S1 each fetch (UI-BR-02).
      const proj = await unwrap<T.ProjectView>(() =>
        client.GET(`${P}/projects/{project_id}`, { params: { path: { project_id: projectId } } }),
      );
      const workflowId = proj.workflowId;
      if (!workflowId) throw new Error('project has no workflow');
      return unwrap<T.WorkflowView>(() =>
        client.GET(`${P}/workflows/{workflow_id}`, { params: { path: { workflow_id: workflowId } } }),
      );
    },
  });

export const stagesOptions = (workflowId: string | undefined) =>
  queryOptions({
    queryKey: qk.stages(workflowId ?? '-'),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<{ items?: T.StageView[] }>(() =>
        client.GET(`${P}/workflows/{workflow_id}/stages`, { params: { path: { workflow_id: workflowId! } } }),
      ),
  });

/**
 * One stage with its run rows. The list view carries no `stageRuns`, so the run log reads this
 * endpoint; `live` keeps it moving while a run is being accepted or is still RUNNING.
 */
export const stageDetailOptions = (stageId: string | undefined, live = false) =>
  queryOptions({
    queryKey: qk.stage(stageId ?? '-'),
    enabled: !!stageId,
    queryFn: () =>
      unwrap<T.StageDetailView>(() =>
        client.GET(`${P}/stages/{stage_id}`, { params: { path: { stage_id: stageId! } } }),
      ),
    refetchInterval: (query) => {
      if (!live) return false;
      const status = String(query.state.data?.status ?? '');
      return status === 'READY' || status === 'PENDING' || status === 'RUNNING' ? 2_000 : false;
    },
  });

export const questionsOptions = (workflowId: string | undefined) =>
  queryOptions({
    queryKey: qk.questions(workflowId ?? '-'),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<{ items?: T.QuestionView[] }>(() =>
        client.GET(`${P}/workflows/{workflow_id}/questions`, { params: { path: { workflow_id: workflowId! } } }),
      ),
  });

export const questionOptions = (questionId: string | undefined) =>
  queryOptions({
    queryKey: qk.question(questionId ?? '-'),
    enabled: !!questionId,
    queryFn: () =>
      unwrap<T.QuestionView>(() =>
        client.GET(`${P}/questions/{question_id}`, { params: { path: { question_id: questionId! } } }),
      ),
  });

export const gatesPendingOptions = () =>
  queryOptions({
    queryKey: qk.gatesPending,
    queryFn: () =>
      unwrap<{ items?: T.GateSummaryView[] }>(() =>
        client.GET(`${P}/gates`, { params: { query: { status: 'WAITING_APPROVAL' } } }),
      ),
  });

/**
 * The approver's own list (S5IF-FLD03): every gate stopped waiting on somebody, across projects.
 *
 * `mine` is S1's role filter (CP-IAM) and the list is paged by `offset`, so neither filter nor order
 * is decided here: filtering after the page arrives would report a count the server never gave, and
 * would quietly drop the gate that is on the next page — the one thing this page exists to prevent.
 */
export const gatesInboxOptions = (
  filters: { mine?: boolean; projectId?: string; limit?: number; offset?: number } = {},
) =>
  queryOptions({
    queryKey: qk.gatesInbox({
      mine: filters.mine ? '1' : undefined,
      projectId: filters.projectId,
      limit: String(filters.limit ?? PAGE_SIZE),
      offset: String(filters.offset ?? 0),
    }),
    queryFn: () =>
      unwrap<T.GateSummaryList>(() =>
        client.GET(`${P}/gates`, {
          params: {
            query: {
              status: 'WAITING_APPROVAL',
              // an unchecked filter leaves the wire entirely (the same shape `notificationsOptions`
              // keeps): `mine=false` and no answer mean different questions to S1's log reader
              mine: filters.mine || undefined,
              projectId: filters.projectId || undefined,
              limit: filters.limit ?? PAGE_SIZE,
              offset: filters.offset ?? 0,
            },
          },
        }),
      ),
  });

export const workflowGatesOptions = (workflowId: string | undefined) =>  queryOptions({
    queryKey: qk.gates(workflowId ?? '-'),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<{ items?: T.GateView[] }>(() =>
        client.GET(`${P}/workflows/{workflow_id}/gates`, { params: { path: { workflow_id: workflowId! } } }),
      ),
  });

export const gateOptions = (gateId: string | undefined) =>
  queryOptions({
    queryKey: qk.gate(gateId ?? '-'),
    enabled: !!gateId,
    queryFn: () =>
      unwrap<T.GateView>(() =>
        client.GET(`${P}/gates/{gate_id}`, { params: { path: { gate_id: gateId! } } }),
      ),
  });

// UI-GATE-001: the six answers the page must show come from S1's explanation, not from the
// reviewer guessing at raw warnings. Under qk.gate's prefix, so gate.* events refresh it.
export const gateExplanationOptions = (gateId: string | undefined) =>
  queryOptions({
    queryKey: qk.gateExplanation(gateId ?? '-'),
    enabled: !!gateId,
    queryFn: () =>
      unwrap<T.GateExplanationView>(() =>
        client.GET(`${P}/gates/{gate_id}/explanation`, { params: { path: { gate_id: gateId! } } }),
      ),
  });

/**
 * The notification feed. `mine` and `unreadOnly` are S1's filters, not local predicates: the feed is
 * paged, so "unread" computed here would mean "unread among the newest page" — a badge that quietly
 * disagrees with the server about how much the operator has to read.
 */
export const notificationsOptions = (
  projectId?: string,
  filters: { mine?: boolean; unreadOnly?: boolean; limit?: number; offset?: number } = {},
) =>
  queryOptions({
    queryKey: qk.notifications({
      projectId,
      mine: filters.mine ? '1' : undefined,
      unreadOnly: filters.unreadOnly ? '1' : undefined,
      limit: String(filters.limit ?? PAGE_SIZE),
      offset: String(filters.offset ?? 0),
    }),
    queryFn: () =>
      unwrap<T.NotificationPage>(() =>
        client.GET(`${P}/notifications`, {
          params: {
            query: {
              projectId,
              mine: filters.mine,
              unreadOnly: filters.unreadOnly,
              limit: filters.limit ?? PAGE_SIZE,
              offset: filters.offset ?? 0,
            },
          },
        }),
      ),
  });

export const environmentsOptions = () =>
  queryOptions({
    queryKey: qk.environments,
    queryFn: () => unwrap<T.EnvironmentPage>(() => client.GET(`${P}/environments`, {})),
  });

/**
 * The cold-start facts behind the getting-started checklist (S5IF-SET01). Facts only: which control
 * is clickable keeps coming from `allowed-actions`, and a section S1 withholds arrives as `null`
 * rather than as a zero the UI would render as "nothing registered".
 */
export const setupOptions = () =>
  queryOptions({
    queryKey: qk.setup,
    queryFn: () => unwrap<T.SetupView>(() => client.GET(`${P}/setup`, {})),
  });

/**
 * Which credentials have already been entered (S5IF-SEC02) — names and timestamps only, because
 * S1 never returns a value, a mask or a length (S5IF-G09). This is the list the "use an existing
 * reference" picker reads, so an operator can point a second environment at a token once.
 */
export const secretsOptions = () =>
  queryOptions({
    queryKey: qk.secrets,
    queryFn: () => unwrap<T.SecretListView>(() => client.GET(`${P}/secrets`, {})),
  });

/**
 * Built-in vendor addresses and model names (S5IF-GOV07). A suggestion, never a verdict: the
 * catalogue carries no reachability field, so a screen cannot draw a green light from it — only
 * the connection test (S5IF-ENV05) and an actual stage run can say whether something answers.
 */
export const providerCatalogueOptions = () =>
  queryOptions({
    queryKey: qk.providerCatalogue,
    queryFn: () =>
      unwrap<T.ProviderCatalogueView>(() => client.GET(`${P}/governance/provider-catalogue`, {})),
  });

/**
 * The agent types S3 can run (S5IF-GOV19). The model form needs them because a model's
 * `applicableAgents` is keyed by agent type — without this list the field could only ever be
 * submitted empty, while the models table already shows it as a column.
 */
export const agentsOptions = () =>
  queryOptions({
    queryKey: qk.agents,
    queryFn: () => unwrap<T.AgentListView>(() => client.GET(`${P}/governance/agents`, {})),
  });

export const expiringCredentialsOptions = () =>
  queryOptions({
    queryKey: qk.expiringCredentials,
    queryFn: () => unwrap<T.ExpiringCredentialList>(() => client.GET(`${P}/environments/expiring-credentials`, {})),
  });

/**
 * Commit windows live here, not on the environment row, and an edit form that cannot read them
 * cannot safely write them either: S4 replaces the whole list, so guessing would drop the stored
 * windows while answering 200.
 */
export const environmentProtectionOptions = (key: string) =>
  queryOptions({
    queryKey: qk.environmentProtection(key),
    queryFn: () =>
      unwrap<T.EnvironmentProtectionView>(() =>
        client.GET(`${P}/environments/{environment_key}/protection`, {
          params: { path: { environment_key: key } },
        }),
      ),
    enabled: key !== '',
  });

export const governanceModelsOptions = () =>
  queryOptions({
    queryKey: qk.governanceModels,
    queryFn: () => unwrap<T.ModelList>(() => client.GET(`${P}/governance/models`, {})),
  });

export const governancePolicyOptions = () =>
  queryOptions({
    queryKey: qk.governancePolicy,
    queryFn: () => unwrap<T.PolicyView>(() => client.GET(`${P}/governance/policy`, {})),
  });

export const governanceProvidersOptions = () =>
  queryOptions({
    queryKey: qk.governanceProviders,
    queryFn: () => unwrap<T.ProviderList>(() => client.GET(`${P}/governance/providers`, {})),
  });

export const governanceUsageOptions = (projectId?: string, groupBy?: string[]) =>
  queryOptions({
    queryKey: qk.governanceUsage(projectId ?? '', groupBy),
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap<T.UsageView>(() =>
        client.GET(`${P}/governance/usage`, {
          params: { query: { projectId, groupBy: groupBy } },
        }),
      ),
  });

/** UI-ADM-005: the budget is per project, and its `state` is S3's verdict, never a local compare. */
export const governanceBudgetOptions = (projectId: string) =>
  queryOptions({
    queryKey: qk.governanceBudget(projectId),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.BudgetView>(() =>
        client.GET(`${P}/governance/budget`, { params: { query: { projectId } } }),
      ),
  });

/**
 * UI-AUTH-001: which identity regime is running, read once per session rather than inferred from
 * a 401. `authMode: off` is the M1/M2 deployment and must keep behaving exactly as before.
 */
export const sessionOptions = () =>
  queryOptions({
    queryKey: qk.session,
    staleTime: 60_000,
    retry: false,
    queryFn: () => unwrap<T.SessionView>(() => client.GET(`${P}/auth/session`, {})),
  });

/** AI-CHAT-003: the session S3 holds for this stage, as S1 projects it. */
export const chatTranscriptOptions = (stageId: string) =>
  queryOptions({
    queryKey: qk.stageChat(stageId),
    enabled: !!stageId,
    queryFn: () =>
      unwrap<T.ChatTranscriptView>(() =>
        client.GET(`${P}/stages/{stage_id}/chat`, { params: { path: { stage_id: stageId } } }),
      ),
  });

/**
 * The account list, one page at a time (S5IF-G04). S1 answers `total`, so the screen can say how
 * many accounts exist while showing only the ones it fetched.
 */
export const usersOptions = (page: { limit?: number; offset?: number } = {}) =>
  queryOptions({
    queryKey: [...qk.users, page.limit ?? PAGE_SIZE, page.offset ?? 0] as const,
    queryFn: () =>
      unwrap<T.UserList>(() =>
        client.GET(`${P}/users`, {
          params: { query: { limit: page.limit ?? PAGE_SIZE, offset: page.offset ?? 0 } },
        }),
      ),
  });

export const rolesOptions = () =>
  queryOptions({
    queryKey: qk.roles,
    queryFn: () => unwrap<T.RoleList>(() => client.GET(`${P}/roles`, {})),
  });

export const membersOptions = (projectId: string) =>
  queryOptions({
    queryKey: qk.members(projectId),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.MemberList>(() =>
        client.GET(`${P}/projects/{project_id}/members`, { params: { path: { project_id: projectId } } }),
      ),
  });

export const artifactGrantsOptions = (projectId: string) =>
  queryOptions({
    queryKey: qk.artifactGrants(projectId),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.GrantList>(() =>
        client.GET(`${P}/projects/{project_id}/artifact-grants`, {
          params: { path: { project_id: projectId } },
        }),
      ),
  });

export const productionExemptionsOptions = (projectId: string) =>
  queryOptions({
    queryKey: qk.productionExemptions(projectId),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.ExemptionList>(() =>
        client.GET(`${P}/projects/{project_id}/production-exemptions`, {
          params: { path: { project_id: projectId } },
        }),
      ),
  });

export const systemSettingsOptions = () =>
  queryOptions({
    queryKey: qk.systemSettings,
    queryFn: () => unwrap<unknown>(() => client.GET(`${P}/settings`, {})),
  });

/**
 * S5IF-BP05: a project whose first design stage has not run has no draft to read, and S1 says so
 * with 404 NOT_GENERATED. That is the state the screen shows, not a request that failed — resolving
 * it to `null` keeps it out of the global toast (UI-REQ-002), while a genuine NOT_FOUND still
 * throws. Every consumer already renders with no draft.
 */
export const blueprintDraftOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.blueprintDraft(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: async () => {
      try {
        return await unwrap<T.DraftView>(() =>
          client.GET(`${P}/projects/{project_id}/blueprint/draft`, { params: { path: { project_id: projectId! } } }),
        );
      } catch (error) {
        // `undefined` is a failed read to TanStack Query; `null` is an answer that says nothing yet
        if (isNotGenerated(error)) return null;
        throw error;
      }
    },
  });

export const sectionSchemaOptions = (projectId: string | undefined, section: string | undefined) =>
  queryOptions({
    queryKey: [...qk.sectionSchemas(projectId ?? '-'), section ?? '-'],
    enabled: !!projectId && !!section,
    queryFn: () =>
      unwrap<T.SectionSchemaView>(() =>
        client.GET(`${P}/projects/{project_id}/blueprint/section-schemas/{section}`, {
          params: { path: { project_id: projectId!, section: section! } },
        }),
      ),
  });

export const registryOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.registry(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.RegistryPage>(() =>
        client.GET(`${P}/projects/{project_id}/registry`, { params: { path: { project_id: projectId! } } }),
      ),
  });

/**
 * S5IF-BP13: the design decisions this blueprint version recorded.
 *
 * `decisionIds` is S1's filter, not a local `filter()`: a Registry row names exactly one decision,
 * and a project with thousands of them should not have to read them all to show one. The ids are
 * part of the query key for the same reason — the two answers are different questions.
 */
export const decisionsOptions = (projectId: string | undefined, decisionIds: string[] = []) =>
  queryOptions({
    queryKey: qk.decisions(projectId ?? '-', decisionIds),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.DecisionList>(() =>
        client.GET(`${P}/projects/{project_id}/decisions`, {
          params: {
            path: { project_id: projectId! },
            query: { decisionIds: decisionIds.length ? decisionIds : undefined },
          },
        }),
      ),
  });

/**
 * UI-REV-002 (BP-TD-009): the dependency graph one build produced, read by workflow — S1
 * deprecated the project-scoped path. The object-type and business-key filters are server-side,
 * because S2 narrows the graph and returns the matched nodes with their one-hop edges, and a
 * 5,000-object model is not a picture the browser can prune itself (NFR-CAP-001, UI-REV-006).
 * `runId` and `blueprintVersion` select the build and are mutually exclusive (S1 answers 422).
 */
export const objectGraphOptions = (
  projectId: string | undefined,
  workflowId: string | undefined,
  filters: { runId?: string; blueprintVersion?: string; businessKeys?: string[]; dhis2Types?: string[] } = {},
) =>
  queryOptions({
    queryKey: qk.objectGraph(
      projectId ?? '-',
      workflowId ?? '',
      filters.runId ?? '',
      filters.blueprintVersion ?? '',
      filters.businessKeys ?? [],
      filters.dhis2Types ?? [],
    ),
    enabled: !!workflowId,
    // search-as-you-type: the previous picture stays on screen while the next one is read,
    // otherwise the first keystroke replaces the very input the operator is using
    placeholderData: keepPreviousData,
    queryFn: () =>
      unwrap<T.WorkflowObjectGraphView>(() =>
        client.GET(`${P}/workflows/{workflow_id}/object-graph`, {
          params: {
            path: { workflow_id: workflowId! },
            query: {
              ...(filters.runId ? { runId: filters.runId } : {}),
              ...(filters.blueprintVersion ? { blueprintVersion: filters.blueprintVersion } : {}),
              ...(filters.businessKeys?.length ? { businessKey: filters.businessKeys } : {}),
              ...(filters.dhis2Types?.length ? { dhis2Type: filters.dhis2Types } : {}),
            },
          },
        }),
      ),
  });

/**
 * S5IF-BP17: the files one build produced (UI-REV-003's by-file view). An empty list is a real
 * answer — nothing was compiled — and the screen has to say that rather than show no rows.
 */
export const packageFilesOptions = (
  projectId: string | undefined,
  version?: string,
  environmentKey?: string,
) =>
  queryOptions({
    queryKey: qk.packageFiles(projectId ?? '-', version ?? '', environmentKey ?? ''),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.PackageFilesView>(() =>
        client.GET(`${P}/projects/{project_id}/package-files`, {
          params: {
            path: { project_id: projectId! },
            query: {
              ...(version ? { version } : {}),
              ...(environmentKey ? { environmentKey } : {}),
            },
          },
        }),
      ),
  });

/**
 * CP-UI-021 (UI-IA-001's Handoff section): what the mode-B development package holds. S1 forwards
 * S2's listing whole and never unpacks it, so the layout on screen is S2's, not ours.
 *
 * `enabled` is the caller's `frontend_package.download` gate: UI-BR-01 does not let the screen decide
 * to ask for a package S1 has already said does not exist.
 */
export const frontendPackageOptions = (projectId: string | undefined, enabled = true) =>
  queryOptions({
    queryKey: qk.frontendPackage(projectId ?? '-'),
    enabled: !!projectId && enabled,
    queryFn: () =>
      unwrap<T.FrontendPackageFilesView>(() =>
        client.GET(`${P}/projects/{project_id}/frontend-package/files`, {
          params: { path: { project_id: projectId! } },
        }),
      ),
  });

/**
 * One file of that package, as text. The download affordance is a plain link to the same URL with
 * `?download=true` — the browser streams a file better than this cache can hold it, and a source
 * file a developer means to open in an editor should not have to pass through React state first.
 */
export const frontendPackageFileOptions = (projectId: string | undefined, path: string | undefined) =>
  queryOptions({
    queryKey: qk.frontendPackageFile(projectId ?? '-', path ?? ''),
    enabled: !!projectId && !!path,
    queryFn: () =>
      unwrap<string>(() =>
        client.GET(`${P}/projects/{project_id}/frontend-package/files/{path}`, {
          params: { path: { project_id: projectId!, path: path! } },
          parseAs: 'text',
        }),
      ),
  });

export const inputsOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.inputs(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.InputPage>(() =>
        client.GET(`${P}/projects/{project_id}/inputs`, { params: { path: { project_id: projectId! } } }),
      ),
  });

/**
 * UI-REQS-001: the browser pre-check has to read the two numbers S1 enforces. They are not in
 * `GET /settings` because that one needs `settings.manage`, and an operator uploading a
 * requirements file is not an administrator.
 */
export const uploadLimitsOptions = () =>
  queryOptions({
    queryKey: qk.uploadLimits,
    queryFn: () => unwrap<T.UploadLimitsView>(() => client.GET(`${P}/upload-limits`, {})),
  });

/** S5IF-INP03: the mapping proposals S3 made for one input, before it is re-parsed. */
export const columnMappingOptions = (projectId: string | undefined, artifactId: string | undefined) =>
  queryOptions({
    queryKey: qk.columnMapping(projectId ?? '-', artifactId ?? '-'),
    enabled: !!projectId && !!artifactId,
    queryFn: () =>
      unwrap<T.ColumnMappingView>(() =>
        client.GET(`${P}/projects/{project_id}/inputs/{artifact_id}/column-mapping`, {
          params: { path: { project_id: projectId!, artifact_id: artifactId! } },
        }),
      ),
  });

/**
 * S5IF-INP08: the model calls one input made, plus the parse status that explains them.
 * While the parse runs (or an attempt has not come back), the page polls: the provider is
 * non-streaming, so a completed attempt is the only new fact between SSE start and finish.
 */
export const llmLogOptions = (projectId: string | undefined, artifactId: string | undefined) =>
  queryOptions({
    queryKey: qk.llmLog(projectId ?? '-', artifactId ?? '-'),
    enabled: !!projectId && !!artifactId,
    queryFn: () =>
      unwrap<T.LlmLogView>(() =>
        client.GET(`${P}/projects/{project_id}/inputs/{artifact_id}/llm-log`, {
          params: { path: { project_id: projectId!, artifact_id: artifactId! } },
        }),
      ),
    refetchInterval: (query) => {
      const data = query.state.data;
      const busy =
        data?.parseStatus === 'PARSING' ||
        (data?.items ?? []).some((item) => item.status === 'IN_FLIGHT');
      return busy ? 3_000 : false;
    },
  });

/** S5IF-INP09: one exchange's request messages and raw answer, fetched on expand. */
export const llmExchangeOptions = (
  projectId: string | undefined,
  artifactId: string | undefined,
  exchangeId: string | undefined,
) =>
  queryOptions({
    queryKey: qk.llmExchange(projectId ?? '-', artifactId ?? '-', exchangeId ?? '-'),
    enabled: !!projectId && !!artifactId && !!exchangeId,
    queryFn: () =>
      unwrap<T.LlmExchangeDetailView>(() =>
        client.GET(`${P}/projects/{project_id}/inputs/{artifact_id}/llm-log/{exchange_id}`, {
          params: {
            path: { project_id: projectId!, artifact_id: artifactId!, exchange_id: exchangeId! },
          },
        }),
      ),
    refetchInterval: (query) => (query.state.data?.status === 'IN_FLIGHT' ? 3_000 : false),
  });

/**
 * S5IF-BP15 / UI-REQS-004: the S3 normalised requirement list, narrowed server-side.
 * Category, source input and confidence are filters S1 applies (CP-UI-003), so the grouped view
 * asks for a category rather than pulling every item and slicing it here.
 */
export const nrrOptions = (
  workflowId: string | undefined,
  filters: { category?: string; minConfidence?: number; inputArtifactId?: string; limit?: number } = {},
) =>
  queryOptions({
    queryKey: qk.nrr(workflowId ?? '-', filters),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<T.NrrPage>(() =>
        client.GET(`${P}/workflows/{workflow_id}/nrr`, {
          params: {
            path: { workflow_id: workflowId! },
            query: {
              category: filters.category,
              minConfidence: filters.minConfidence,
              inputArtifactId: filters.inputArtifactId,
              // The review card is meant to show the parse, not the first page of it; the server
              // caps this at 200 and still answers `total`, so the card can say what is left.
              limit: filters.limit ?? 200,
            },
          },
        }),
      ),
  });

/** S5IF-INP06: an agent run's live status, including the progress S1 takes from the event stream. */
export const agentRunOptions = (workflowId: string | undefined, runId: string | undefined, live = false) =>
  queryOptions({
    queryKey: qk.agentRun(workflowId ?? '-', runId ?? '-'),
    enabled: !!workflowId && !!runId,
    queryFn: () =>
      unwrap<T.AgentRunView>(() =>
        client.GET(`${P}/workflows/{workflow_id}/agent-runs/{run_id}`, {
          params: { path: { workflow_id: workflowId!, run_id: runId! } },
        }),
      ),
    refetchInterval: (query) => {
      if (!live) return false;
      const status = String(query.state.data?.status ?? '');
      return status === 'RUNNING' || status === 'WAITING_INPUT' ? 2_000 : false;
    },
  });


/**
 * UI-REV-001: the artifacts a workflow produced, newest or by type. S1 owns the list; the
 * review screen never assembles it from stage rows.
 */
export const workflowArtifactsOptions = (
  workflowId: string | undefined,
  filters: { type?: string; limit?: number; sortBy?: string; sortDir?: string } = {},
) =>
  queryOptions({
    queryKey: qk.workflowArtifacts(workflowId ?? '-', { type: filters.type, sortBy: filters.sortBy, sortDir: filters.sortDir }),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<T.ArtifactPage>(() =>
        client.GET(`${P}/workflows/{workflow_id}/artifacts`, {
          params: {
            path: { workflow_id: workflowId! },
            query: { type: filters.type, limit: filters.limit, sortBy: filters.sortBy, sortDir: filters.sortDir },
          },
        }),
      ),
  });

/**
 * The review feed: the same route, walked by cursor. A workflow with 300 artifacts is the normal
 * case at NFR-CAP-001 scale, and a list that silently stops at the first page reads as "these are
 * all there are".
 */
export const artifactFeedOptions = (workflowId: string | undefined, type?: string) =>
  infiniteQueryOptions({
    queryKey: [...qk.workflowArtifacts(workflowId ?? '-', { type }), 'feed'] as const,
    enabled: !!workflowId,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      unwrap<T.ArtifactPage>(() =>
        client.GET(`${P}/workflows/{workflow_id}/artifacts`, {
          params: {
            path: { workflow_id: workflowId! },
            query: { type, limit: PAGE_SIZE, cursor: pageParam },
          },
        }),
      ),
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });

export const artifactOptions = (artifactId: string | undefined) =>
  queryOptions({
    queryKey: qk.artifact(artifactId ?? '-'),
    enabled: !!artifactId,
    queryFn: () =>
      unwrap<T.ArtifactView>(() =>
        client.GET(`${P}/artifacts/{artifact_id}`, { params: { path: { artifact_id: artifactId! } } }),
      ),
  });

/**
 * Raw content, read as text: S1 answers `application/octet-stream`, so the typed client must be
 * told not to parse JSON. A purged artifact (FR-DATA-008) is an error here, not an empty body.
 */
export const artifactContentOptions = (artifactId: string | undefined) =>
  queryOptions({
    queryKey: qk.artifactContent(artifactId ?? '-'),
    enabled: !!artifactId,
    queryFn: () =>
      unwrap<string>(() =>
        client.GET(`${P}/artifacts/{artifact_id}/content`, {
          params: { path: { artifact_id: artifactId! } },
          parseAs: 'text',
        }),
      ),
  });

export const artifactDiffOptions = (artifactId: string | undefined, against: string | undefined) =>
  queryOptions({
    queryKey: qk.artifactDiff(artifactId ?? '-', against ?? '-'),
    enabled: !!artifactId && !!against,
    queryFn: () =>
      unwrap<T.ArtifactDiffView>(() =>
        client.GET(`${P}/artifacts/{artifact_id}/diff`, {
          params: { path: { artifact_id: artifactId! }, query: { against: against! } },
        }),
      ),
  });

/** UI-REV-001: what an artifact was built from and what was built from it. */
export const artifactLineageOptions = (artifactId: string | undefined) =>
  queryOptions({
    queryKey: qk.artifactLineage(artifactId ?? '-'),
    enabled: !!artifactId,
    queryFn: () =>
      unwrap<T.LineageView>(() =>
        client.GET(`${P}/artifacts/{artifact_id}/lineage`, {
          params: { path: { artifact_id: artifactId! } },
        }),
      ),
  });

/** UI-REV-005: validation issues, filtered server-side by level / stage / rule / object. */
export const validationIssuesOptions = (
  workflowId: string | undefined,
  filters: { level?: string; sourceStage?: string; ruleId?: string; businessKey?: string } = {},
) =>
  queryOptions({
    // the filters belong in the key: without them a changed filter is the same query and TanStack
    // Query serves the previous answer instead of asking S1 again.
    queryKey: qk.validationIssues(workflowId ?? '-', filters),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<T.ValidationIssuePage>(() =>
        client.GET(`${P}/workflows/{workflow_id}/validation-issues`, {
          params: {
            path: { workflow_id: workflowId! },
            query: {
              level: filters.level,
              sourceStage: filters.sourceStage,
              ruleId: filters.ruleId,
              businessKey: filters.businessKey,
            },
          },
        }),
      ),
  });

/** UI-REV-003: two Blueprint versions compared, object by object. */
export const blueprintDiffOptions = (
  projectId: string | undefined,
  fromVersion: string | undefined,
  toVersion: string | undefined,
) =>
  queryOptions({
    queryKey: qk.blueprintDiff(projectId ?? '-', fromVersion ?? '-', toVersion ?? '-'),
    enabled: !!projectId && !!fromVersion && !!toVersion,
    queryFn: () =>
      unwrap<T.BlueprintDiffView>(() =>
        client.GET(`${P}/projects/{project_id}/blueprint/diff`, {
          params: { path: { project_id: projectId! }, query: { fromVersion: fromVersion!, toVersion: toVersion! } },
        }),
      ),
  });

export const blueprintVersionsOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.blueprintVersions(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.BlueprintVersionPage>(() =>
        client.GET(`${P}/projects/{project_id}/blueprint/versions`, {
          params: { path: { project_id: projectId! } },
        }),
      ),
  });

/** S5IF-BP04: raw section entries with S2's own `_section` / `_path` locators kept verbatim. */
export const blueprintEntriesOptions = (projectId: string | undefined, sections?: string[]) =>
  queryOptions({
    queryKey: qk.blueprintEntries(projectId ?? '-', sections),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.EntryPage>(() =>
        client.GET(`${P}/projects/{project_id}/blueprint/entries`, {
          params: { path: { project_id: projectId! }, query: { sections } },
        }),
      ),
  });

export const recentRunsOptions = () =>
  queryOptions({
    queryKey: qk.runList(),
    queryFn: () =>
      unwrap<{ items?: T.RunView[] }>(() => client.GET(`${P}/deployment-runs`, {})),
  });

export const deploymentRunsOptions = (workflowId: string | undefined) =>  queryOptions({
    queryKey: qk.deploymentRuns(workflowId ?? '-'),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<{ items?: T.RunView[] }>(() =>
        client.GET(`${P}/workflows/{workflow_id}/deployment-runs`, { params: { path: { workflow_id: workflowId! } } }),
      ),
  });

/** UI-OPS-002: S0's own query fields, exposed by S1 so the filter narrows the query, not the page. */
export interface AuditFilters {
  eventType?: string;
  operatorName?: string;
  objectId?: string;
  occurredAfter?: string;
  occurredBefore?: string;
}

/**
 * One page of the audit trail, read by cursor (S5IF-G04).
 *
 * An infinite query rather than a big `limit`: the trail is the longest list in the system, and
 * "the newest 50" is not the same question as "everything this operator changed last week" — the
 * filters go to S1 and the cursor walks the *filtered* result, which is the only way the answer
 * stays honest as the trail grows.
 */
export const auditOptions = (projectId?: string, filters: AuditFilters = {}) =>
  infiniteQueryOptions({
    queryKey: [...qk.audit(projectId ?? 'global'), filters] as const,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => {
      const query = {
        eventType: filters.eventType || undefined,
        operatorName: filters.operatorName || undefined,
        objectId: filters.objectId || undefined,
        occurredAfter: filters.occurredAfter || undefined,
        occurredBefore: filters.occurredBefore || undefined,
        limit: PAGE_SIZE,
        cursor: pageParam,
      };
      return projectId
        ? unwrap<AuditPage>(() =>
            client.GET(`${P}/projects/{project_id}/audit`, {
              params: { path: { project_id: projectId }, query },
            }),
          )
        : unwrap<AuditPage>(() => client.GET(`${P}/audit`, { params: { query } }));
    },
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });

export const changeRequestsOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.changeRequests(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () => unwrap<{ items?: T.ChangeRequestView[] }>(() =>
      client.GET(`${P}/projects/{project_id}/change-requests`, { params: { path: { project_id: projectId! } } }),
    ),
  });

/**
 * UI-OPS-002: the COMMIT trace for one execution — the gate approvals, the package hash, the
 * import report and the audit records that tie them together. S1 assembles it across subsystems;
 * S5 renders it unchanged.
 */
export const commitTraceOptions = (executionId: string | undefined) =>
  queryOptions({
    queryKey: [...qk.audit('commit-trace'), executionId ?? '-'] as const,
    enabled: !!executionId,
    queryFn: () =>
      unwrap<T.CommitTraceView>(() =>
        client.GET(`${P}/audit/commit-trace/{execution_id}`, {
          params: { path: { execution_id: executionId! } },
        }),
      ),
  });

export const runSummaryOptions = (runId: string | undefined) =>
  queryOptions({
    queryKey: qk.runSummary(runId ?? '-'),
    enabled: !!runId,
    queryFn: () =>
      unwrap<T.RunSummaryView>(() =>
        client.GET(`${P}/deployment-runs/{run_id}/summary`, { params: { path: { run_id: runId! } } }),
      ),
  });

/**
 * UI-DEP-003: the bindings a Run still cannot resolve, and the objects in the stored
 * discovery result a hand-set binding could point at (S5IF-BP09 / S5IF-BP11). The candidate
 * list is searched server-side precisely so a 5,000-object environment is not shipped whole.
 */
export const bindingsOptions = (runId: string | undefined) =>
  queryOptions({
    queryKey: qk.bindings(runId ?? '-'),
    enabled: !!runId,
    queryFn: () =>
      unwrap<T.BindingPage>(() =>
        client.GET(`${P}/deployment-runs/{run_id}/environment-bindings`, {
          params: { path: { run_id: runId! } },
        }),
      ),
  });

/**
 * UI-DEP-005: what the execution actually did. S1 projects S4's outcome, including the
 * normalised issues; the report and ImportReport documents themselves are artifacts, read by
 * id from here (S5IF-FLD05).
 */
export const executionOptions = (executionId: string | undefined) =>
  queryOptions({
    queryKey: qk.execution(executionId ?? '-'),
    enabled: !!executionId,
    queryFn: () =>
      unwrap<T.ExecutionView>(() =>
        client.GET(`${P}/executions/{execution_id}`, { params: { path: { execution_id: executionId! } } }),
      ),
  });

export const executionReportOptions = (executionId: string | undefined) =>
  queryOptions({
    queryKey: qk.executionReport(executionId ?? '-'),
    enabled: !!executionId,
    queryFn: () =>
      unwrap<T.ExecutionReportView>(() =>
        client.GET(`${P}/executions/{execution_id}/report`, { params: { path: { execution_id: executionId! } } }),
      ),
  });

export const bindingCandidatesOptions = (
  runId: string | undefined,
  businessKey: string | undefined,
  query: string,
) =>
  queryOptions({
    queryKey: qk.bindingCandidates(runId ?? '-', businessKey ?? '-', query),
    enabled: !!runId && !!businessKey,
    queryFn: () =>
      unwrap<T.CandidatePage>(() =>
        client.GET(`${P}/deployment-runs/{run_id}/environment-bindings/{business_key}/candidates`, {
          params: {
            path: { run_id: runId!, business_key: businessKey! },
            query: query ? { query } : undefined,
          },
        }),
      ),
  });

/** UI-PRJ-004: the three project-scoped reads the settings page owns. None of them has a local
 *  default — an unset reminder is shown as unset, because a value invented here would be saved
 *  back over the server's by the next operator who pressed Save. */
export const projectSettingsOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.projectSettings(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.ProjectSettingsView>(() =>
        client.GET(`${P}/projects/{project_id}/settings`, { params: { path: { project_id: projectId! } } }),
      ),
  });

export const targetEnvironmentsOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.targetEnvironments(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.TargetEnvironmentListView>(() =>
        client.GET(`${P}/projects/{project_id}/target-environments`, { params: { path: { project_id: projectId! } } }),
      ),
  });

export const uidPoolOptions = (projectId: string | undefined) =>
  queryOptions({
    queryKey: qk.uidPool(projectId ?? '-'),
    enabled: !!projectId,
    queryFn: () =>
      unwrap<T.UidPoolView>(() =>
        client.GET(`${P}/projects/{project_id}/uid-pool`, { params: { path: { project_id: projectId! } } }),
      ),
  });

/** UI-ADM-001: one environment's capability profile and protection settings. The profile key has
 *  lived in `queryKeys` unused for a while — the screen that reads it did not exist. */
export const capabilityProfileOptions = (environmentKey: string | undefined) =>
  queryOptions({
    queryKey: qk.capabilityProfile(environmentKey ?? '-'),
    enabled: !!environmentKey,
    queryFn: () =>
      unwrap<T.CapabilityProfileView>(() =>
        client.GET(`${P}/environments/{environment_key}/capability-profile`, {
          params: { path: { environment_key: environmentKey! } },
        }),
      ),
  });

export const environmentOptions = (environmentKey: string | undefined) =>
  queryOptions({
    queryKey: qk.environment(environmentKey ?? '-'),
    enabled: !!environmentKey,
    queryFn: () =>
      unwrap<T.EnvironmentView>(() =>
        client.GET(`${P}/environments/{environment_key}`, {
          params: { path: { environment_key: environmentKey! } },
        }),
      ),
  });

/** UI-ADM-003: what may be activated, and the two facts that decide whether it may.
 *  Both lists are read once and matched locally — asking per prompt version would be one request
 *  per row on a screen that already shows dozens. */
export const governancePromptsOptions = () =>
  queryOptions({
    queryKey: qk.governancePrompts,
    queryFn: () => unwrap<T.PromptList>(() => client.GET(`${P}/governance/prompts`, {})),
  });

export const governanceEvaluationsOptions = () =>
  queryOptions({
    queryKey: qk.governanceEvaluations,
    queryFn: () => unwrap<T.EvaluationList>(() => client.GET(`${P}/governance/evaluations`, {})),
  });

export const governanceRegressionsOptions = () =>
  queryOptions({
    queryKey: qk.governanceRegressions,
    queryFn: () => unwrap<T.RegressionList>(() => client.GET(`${P}/governance/regression-records`, {})),
  });
