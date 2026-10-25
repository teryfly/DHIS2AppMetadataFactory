import { useMutation, useQueryClient, type QueryKey } from '@tanstack/vue-query';
import { request, type RequestOptions } from '@/api/http';
import { AppError } from '@/api/errors';
import { actionIntent, newUuid, releaseIdempotencyKey, hashJson, payloadDigest } from '@/api/idempotency';
import { qk } from '@/api/keys';
import type * as T from '@/api/types';

type Method = 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'GET';
type InvalidateFn<V> = (v: V) => QueryKey[];

/**
 * Whether a failed attempt still owns its idempotency key.
 *
 * S1 replays a completed key byte-for-byte, and the same key with a corrected body comes back
 * 422 IDEMPOTENCY_CONFLICT (CP-API-003). So a key whose request was *answered* with a refusal is
 * spent, and keeping it pins every later attempt of that action to the old refusal. Only an
 * outcome nobody knows keeps its key: a network failure (the request may have landed) and
 * REQUEST_IN_PROGRESS (the server is still working on that exact request — a fresh key there
 * would run the action twice, which CP-GATE-018 forbids for COMMIT).
 */
export function keepsIdempotencyKey(error: unknown): boolean {
  if (!(error instanceof AppError)) return true;
  return error.networkError || error.errorCode === 'REQUEST_IN_PROGRESS';
}

function write<V>(
  qc: ReturnType<typeof useQueryClient>,
  method: Method,
  path: (v: V) => string,
  body: ((v: V) => unknown) | undefined,
  intent: ((v: V) => string | undefined) | undefined,
  invalidates: InvalidateFn<V>,
  refreshActions?: (v: V) => string | undefined,
  query?: (v: V) => RequestOptions['query'],
) {
  return useMutation({
    mutationFn: (v: V): Promise<unknown> => {
      const it = intent?.(v);
      return request<unknown>(method, path(v), { body: body?.(v), query: query?.(v), intent: it });
    },
    onSuccess: (_d, v) => {
      const it = intent?.(v);
      if (it) releaseIdempotencyKey(it);
      for (const key of invalidates(v)) qc.invalidateQueries({ queryKey: key, exact: false });
      const wf = refreshActions?.(v);
      if (wf) qc.invalidateQueries({ queryKey: qk.allowedActions(wf), exact: false });
      qc.invalidateQueries({ queryKey: qk.platformActions, exact: false });
      // Same reasoning as the platform answer one line above: registering an environment, passing a
      // connection test, adding a model or creating a project all change what the guided checklist
      // is allowed to call "ready" (S5IF-SET01), and it must not lag behind the write that did it.
      qc.invalidateQueries({ queryKey: qk.setup, exact: false });
    },
    onError: (err, v) => {
      const it = intent?.(v);
      if (it && !keepsIdempotencyKey(err)) releaseIdempotencyKey(it);
      // On conflict / invalidated approval, refresh allowed-actions + workflow BEFORE surfacing the
      // toast, so the buttons reflect the newest server state (UM-NET-001).
      if (err instanceof AppError && (err.isConflict || err.isApprovalInvalidated)) {
        for (const key of invalidates(v)) qc.invalidateQueries({ queryKey: key, exact: false });
        const wf = refreshActions?.(v);
        if (wf) qc.invalidateQueries({ queryKey: qk.allowedActions(wf), exact: false });
        qc.invalidateQueries({ queryKey: ['allowed-actions'], exact: false });
      }
    },
  });
}

// -------------------------------------------------------------------- project
export function useCreateProject() {
  const qc = useQueryClient();
  return write<{ projectId?: string; body: T.CreateProjectBody }>(
    qc,
    'POST',
    () => '/projects',
    (v) => v.body,
    (v) => actionIntent('POST', '/projects', v.projectId ?? 'new'),
    () => [qk.projectsList],
  );
}

export function useArchiveProject() {
  const qc = useQueryClient();
  return write<string>(qc, 'POST', (id) => `/projects/${encodeURIComponent(id)}/archive`, undefined, (id) => actionIntent('POST', '/projects/archive', id), (id) => [qk.project(id), qk.projectsList]);
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return write<string>(qc, 'DELETE', (id) => `/projects/${encodeURIComponent(id)}`, undefined, (id) => actionIntent('DELETE', '/projects/delete', id), () => [qk.projectsList]);
}

export function useExportProject() {
  const qc = useQueryClient();
  return write<string>(qc, 'GET', (id) => `/projects/${encodeURIComponent(id)}/export`, undefined, undefined, (id) => [qk.project(id)]);
}

/**
 * UI-PRJ-004 / CP-PRJ-009: restoring a project from an exported artifact. A 202 with the restored
 * project's id — the import itself is a step, so the list is what has to be re-read, not a row this
 * answer can render.
 */
export function useImportProject() {
  const qc = useQueryClient();
  return write<{ artifactId: string }>(
    qc,
    'POST',
    () => '/projects/import',
    (v) => ({ artifactId: v.artifactId }),
    (v) => actionIntent('POST', '/projects/import', v.artifactId, payloadDigest(JSON.stringify(v))),
    () => [qk.projectsList],
  );
}

export function useSaveProjectSettings() {
  const qc = useQueryClient();
  return write<{ projectId: string; body: T.ProjectSettingsBody }>(
    qc,
    'PUT',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/settings`,
    (v) => v.body,
    (v) => actionIntent('PUT', '/projects/{project_id}/settings', v.projectId, payloadDigest(JSON.stringify(v.body))),
    (v) => [qk.projectSettings(v.projectId), qk.project(v.projectId)],
  );
}

export function useUpdateTargetEnvironments() {
  const qc = useQueryClient();
  return write<{ projectId: string; body: unknown }>(
    qc,
    'PUT',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/target-environments`,
    (v) => v.body,
    (v) => actionIntent('PUT', '/projects/{project_id}/target-environments', v.projectId, payloadDigest(JSON.stringify(v.body))),
    (v) => [qk.targetEnvironments(v.projectId), qk.project(v.projectId), qk.workflowOfProject(v.projectId)],
  );
}

// -------------------------------------------------------------------- stage run / approval
export function useRunStage(workflowId?: () => string | undefined) {
  const qc = useQueryClient();
  const wf = workflowId ?? (() => undefined);
  return write<{ stageId: string; rerun?: boolean }>(
    qc,
    'POST',
    (v) => `/stages/${encodeURIComponent(v.stageId)}/${v.rerun ? 'rerun' : 'run'}`,
    undefined,
    // No idempotency key: S1 dropped it for run/rerun.
    undefined,
    (v) => [qk.stage(v.stageId)],
    wf,
  );
}

export function useSubmitForApproval(wf?: () => string | undefined) {
  const qc = useQueryClient();
  return write<{ stageId: string }>(
    qc,
    'POST',
    (v) => `/stages/${encodeURIComponent(v.stageId)}/submit-for-approval`,
    undefined,
    (v) => actionIntent('POST', '/stages/submit-for-approval', v.stageId),
    (v) => [qk.stage(v.stageId), qk.gatesPending],
    wf,
  );
}

export function useResolveManualCheck(wf?: () => string | undefined) {
  const qc = useQueryClient();
  return write<{ stageId: string; body: T.ManualCheckBody }>(
    qc,
    'POST',
    (v) => `/stages/${encodeURIComponent(v.stageId)}/resolve-manual-check`,
    (v) => v.body,
    (v) => actionIntent('POST', '/stages/resolve-manual-check', v.stageId, String(v.body?.outcome ?? '')),
    (v) => [qk.stage(v.stageId)],
    wf,
  );
}

/**
 * `POST /stages/{id}/patches` — the only write that reaches a blueprint draft.
 *
 * `projectId` is optional because not every caller has one on hand; it is what makes the draft
 * reads refresh, and a screen that edits entries without passing it would keep showing the list it
 * submitted. The artifact invalidation names the workflow: `qk.workflowArtifacts('*')` never
 * matched anything, because this TanStack version prefix-matches a shorter filter key and has no
 * wildcard of its own.
 */
export function useSubmitPatch(wf?: () => string | undefined) {
  const qc = useQueryClient();
  return write<{ stageId: string; body: T.StagePatchBody; projectId?: string }>(
    qc,
    'POST',
    (v) => `/stages/${encodeURIComponent(v.stageId)}/patches`,
    (v) => v.body,
    (v) => actionIntent('POST', '/stages/patches', v.stageId, hashJson(v.body)),
    (v) => {
      const workflowId = wf?.();
      return [
        qk.stage(v.stageId),
        ...(workflowId ? [qk.workflowArtifacts(workflowId)] : []),
        ...(v.projectId ? [qk.blueprintEntries(v.projectId), qk.blueprintDraft(v.projectId)] : []),
      ];
    },
    wf,
  );
}

// -------------------------------------------------------------------- workflow control
/**
 * Pause / resume / cancel the whole workflow. One hook for the three verbs because the screen that
 * offers them shows a single control whose name follows `paused`; the verb is part of the intent so
 * a retried pause can never be replayed as a resume.
 */
export function useWorkflowFlag() {
  const qc = useQueryClient();
  return write<{ workflowId: string; projectId: string; flag: 'pause' | 'resume' | 'cancel' }>(
    qc,
    'POST',
    (v) => `/workflows/${encodeURIComponent(v.workflowId)}/${v.flag}`,
    undefined,
    (v) => actionIntent('POST', `/workflows/${v.flag}`, v.workflowId),
    (v) => [qk.workflow(v.workflowId), qk.workflowOfProject(v.projectId), qk.stages(v.workflowId)],
    (v) => v.workflowId,
  );
}

// -------------------------------------------------------------------- questions
/**
 * The questionnaire's one submit (CP-Q-005). All answers travel together because the run resumes
 * once, after the last answer — answering one at a time would let the model continue past a
 * question the person had not reached yet. The answer list is part of the idempotency intent:
 * a corrected submission is a different action, not a replay of the refused one.
 */
export function useAnswerQuestions() {
  const qc = useQueryClient();
  return write<{ workflowId: string; body: T.AnswerQuestionsBody }>(
    qc,
    'POST',
    () => '/questions/answers',
    (v) => v.body,
    (v) => actionIntent('POST', '/questions/answers', v.workflowId, payloadDigest(JSON.stringify(v.body))),
    (v) => [
      qk.questions(v.workflowId),
      qk.stages(v.workflowId),
      qk.workflowGates(v.workflowId),
      qk.gatesPending,
      qk.openQuestions,
      ...v.body.answers.map((answer) => qk.question(answer.questionId)),
    ],
    (v) => v.workflowId,
  );
}

export function useAnswerQuestion(wf?: () => string | undefined) {
  const qc = useQueryClient();
  return write<{ questionId: string; workflowId?: string; body: T.AnswerQuestionBody }>(
    qc,
    'POST',
    (v) => `/questions/${encodeURIComponent(v.questionId)}/answer`,
    (v) => v.body,
    (v) => actionIntent('POST', '/questions/answer', v.questionId, v.body.suggestionId ?? v.body.customText ?? ''),
    (v) => [qk.question(v.questionId), qk.openQuestions, ...(v.workflowId ? [qk.questions(v.workflowId)] : [])],
    wf,
  );
}

// -------------------------------------------------------------------- gates
export function useApproveGate(wf?: () => string | undefined) {
  const qc = useQueryClient();
  return write<{ gateId: string; body: T.ApproveGateBody }>(
    qc,
    'POST',
    (v) => `/gates/${encodeURIComponent(v.gateId)}/approve`,
    (v) => v.body,
    (v) => actionIntent('POST', '/gates/approve', v.gateId, hashJson(v.body)),
    (v) => [qk.gate(v.gateId), qk.gatesPending],
    wf,
  );
}

export function useRejectGate(wf?: () => string | undefined) {
  const qc = useQueryClient();
  return write<{ gateId: string; body: T.RejectGateBody }>(
    qc,
    'POST',
    (v) => `/gates/${encodeURIComponent(v.gateId)}/reject`,
    (v) => v.body,
    (v) => actionIntent('POST', '/gates/reject', v.gateId, hashJson(v.body)),
    (v) => [qk.gate(v.gateId), qk.gatesPending],
    wf,
  );
}

// -------------------------------------------------------------------- deployments
export function useCreateRun() {
  const qc = useQueryClient();
  return write<{ workflowId: string; environmentKey: string }>(
    qc,
    'POST',
    (v) => `/workflows/${encodeURIComponent(v.workflowId)}/deployment-runs`,
    (v) => ({ environmentKey: v.environmentKey }) as T.CreateRunBody,
    (v) => actionIntent('POST', '/deployment-runs/create', v.workflowId, v.environmentKey),
    (v) => [qk.deploymentRuns(v.workflowId)],
    () => undefined,
  );
}

/**
 * COMMIT. Retries reuse the same idempotency key; a network failure keeps the key and shows
 * "结果待确认" without an automatic resubmit (UM-BR-05, UM-AC-14).
 */
export function useCommitRun() {
  const qc = useQueryClient();
  return write<string>(
    qc,
    'POST',
    (id) => `/deployment-runs/${encodeURIComponent(id)}/commit`,
    undefined,
    (id) => actionIntent('POST', '/deployment-runs/commit', id),
    (id) => [qk.run(id), qk.runSummary(id), qk.recentRuns],
  );
}

/**
 * Stop a deployment run that has not committed. The workflow id travels with it because a cancelled
 * run changes both the run list and what S1 says the operator may still do.
 */
export function useCancelRun() {
  const qc = useQueryClient();
  return write<{ runId: string; workflowId: string }>(
    qc,
    'POST',
    (v) => `/deployment-runs/${encodeURIComponent(v.runId)}/cancel`,
    undefined,
    (v) => actionIntent('POST', '/deployment-runs/cancel', v.runId),
    (v) => [qk.run(v.runId), qk.runSummary(v.runId), qk.recentRuns, qk.deploymentRuns(v.workflowId)],
    (v) => v.workflowId,
  );
}

/**
 * Stop one running Agent run (the endpoint is `POST /workflows/{wf}/agent-runs/{run}/cancel`).
 * The run id comes off the SSE `agent.progress` frame — without it the button has nothing to address.
 */
export function useCancelAgentRun() {
  const qc = useQueryClient();
  return write<{ workflowId: string; projectId: string; runId: string; stageId?: string }>(
    qc,
    'POST',
    (v) => `/workflows/${encodeURIComponent(v.workflowId)}/agent-runs/${encodeURIComponent(v.runId)}/cancel`,
    undefined,
    (v) => actionIntent('POST', '/workflows/agent-runs/cancel', v.runId),
    (v) => [qk.workflow(v.workflowId), qk.stages(v.workflowId), ...(v.projectId ? [qk.workflowOfProject(v.projectId)] : []), ...(v.stageId ? [qk.stage(v.stageId)] : [])],
    (v) => v.workflowId,
  );
}

export function useDriftCheck() {
  const qc = useQueryClient();
  return write<string>(qc, 'POST', (id) => `/deployment-runs/${encodeURIComponent(id)}/drift-check`, undefined, (id) => actionIntent('POST', '/deployment-runs/drift-check', id), (id) => [qk.runSummary(id), qk.run(id)]);
}

export function useSetBinding() {
  const qc = useQueryClient();
  return write<{ runId: string; businessKey: string; body: unknown }>(
    qc,
    'PUT',
    (v) => `/deployment-runs/${encodeURIComponent(v.runId)}/environment-bindings/${encodeURIComponent(v.businessKey)}`,
    (v) => v.body,
    (v) => actionIntent('PUT', '/deployment-runs/binding', v.runId, v.businessKey),
    (v) => [qk.runBindings(v.runId), qk.runSummary(v.runId)],
  );
}

// -------------------------------------------------------------------- inputs
export function useParseInput() {
  const qc = useQueryClient();
  return write<{ projectId: string; artifactId: string; body?: T.ParseInputRequest; mappingDigest?: string }>(
    qc,
    'POST',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/inputs/${encodeURIComponent(v.artifactId)}/parse`,
    (v) => v.body,
    (v) => actionIntent('POST', '/inputs/parse', v.artifactId, v.mappingDigest ?? ''),
    // A parse can add questions and a fresh NRR. The list is workflow-scoped and this hook only
    // knows the project, so the workflow prefix is what refreshes both the review card and the
    // to-do centre without a reload.
    (v) => [qk.inputs(v.projectId), qk.columnMapping(v.projectId, v.artifactId), qk.openQuestions, ['workflows']],
  );
}

// -------------------------------------------------------------------- artifacts
export function useExplainArtifact() {
  const qc = useQueryClient();
  return write<{ artifactId: string; body: unknown }>(qc, 'POST', (v) => `/artifacts/${encodeURIComponent(v.artifactId)}/explain`, (v) => v.body, undefined, (v) => [qk.artifactContent(v.artifactId)]);
}

export function useReproduceArtifact() {
  const qc = useQueryClient();
  return write<string>(qc, 'POST', (id) => `/artifacts/${encodeURIComponent(id)}/reproduce`, undefined, undefined, (id) => [qk.artifact(id)]);
}

/**
 * FR-DATA-008: clear an artifact's bytes and keep its record. S1's route asks for no body and no
 * idempotency key, so the row and the (now refused) content are both dropped from the cache — the
 * screen must re-read rather than keep showing bytes it no longer has.
 */
export function usePurgeArtifact() {
  const qc = useQueryClient();
  return write<string>(
    qc,
    'POST',
    (id) => `/artifacts/${encodeURIComponent(id)}/purge`,
    undefined,
    undefined,
    (id) => [qk.artifact(id), qk.artifactContent(id)],
  );
}

// -------------------------------------------------------------------- notifications
export function useMarkNotificationRead() {
  const qc = useQueryClient();
  return write<string>(qc, 'POST', (id) => `/notifications/${encodeURIComponent(id)}/read`, undefined, undefined, () => [qk.notificationsList], undefined, () => ({ projectId: undefined }));
}

// -------------------------------------------------------------------- environments / governance
export function useTestConnection() {
  const qc = useQueryClient();
  return write<{ environmentKey: string; body?: T.TestConnectionRequest }>(
    qc,
    'POST',
    (v) => `/environments/${encodeURIComponent(v.environmentKey)}/test-connection`,
    (v) => v.body,
    (v) => actionIntent('POST', '/environments/test-connection', v.environmentKey),
    (v) => [qk.environment(v.environmentKey)],
  );
}

/** Switch an environment out of service. S4 keeps the row; the reason is optional but recorded. */
export function useDisableEnvironment() {
  const qc = useQueryClient();
  return write<{ environmentKey: string; reason?: string }>(
    qc,
    'POST',
    (v) => `/environments/${encodeURIComponent(v.environmentKey)}/disable`,
    (v) => ({ reason: v.reason }),
    (v) => actionIntent('POST', '/environments/disable', v.environmentKey, hashJson({ reason: v.reason ?? '' })),
    (v) => [qk.environment(v.environmentKey), qk.environments],
  );
}

/** Ask the instance what it is; S1 stores the answer as a capability profile (DX-ENV-004). */
export function useProbeCapabilities() {
  const qc = useQueryClient();
  return write<{ environmentKey: string }>(
    qc,
    'POST',
    (v) => `/environments/${encodeURIComponent(v.environmentKey)}/probe-capabilities`,
    undefined,
    (v) => actionIntent('POST', '/environments/probe-capabilities', v.environmentKey),
    (v) => [qk.environment(v.environmentKey), qk.environments],
  );
}

export function useEmergencyLock() {
  const qc = useQueryClient();
  return write<{ environmentKey: string; locked: boolean; reason: string }>(
    qc,
    'POST',
    (v) => `/environments/${encodeURIComponent(v.environmentKey)}/emergency-lock`,
    // S1's field is `locked` (no default) and a lock demands a `reason`; sending `lock` was
    // dropped as an unknown key and the request failed validation.
    (v) => ({ locked: v.locked, reason: v.reason } as unknown as T.EmergencyLockRequest),
    // the reason is part of the logical action: locking twice with different reasons under one
    // key is IDEMPOTENCY_CONFLICT, and the operator would see an error about a key they never typed
    (v) => actionIntent('POST', '/environments/emergency-lock', v.environmentKey, hashJson({ locked: v.locked, reason: v.reason })),
    (v) => [qk.environment(v.environmentKey), qk.environments],
  );
}

export function useCreateEnvironment() {
  const qc = useQueryClient();
  // the body is part of the intent: a create that failed and is retried *after an edit* is a
  // different logical action, and one key over two bodies reads as IDEMPOTENCY_CONFLICT
  return write<T.CreateEnvironmentRequest>(qc, 'POST', () => '/environments', (v) => v, (v) => actionIntent('POST', '/environments', v.environmentKey, hashJson(v)), () => [qk.environments]);
}

export function useUpdateEnvironment() {
  const qc = useQueryClient();
  return write<{ environmentKey: string; body: T.UpdateEnvironmentRequest }>(
    qc,
    'PATCH',
    (v) => `/environments/${encodeURIComponent(v.environmentKey)}`,
    (v) => v.body,
    // the body goes into the intent so a second submit of an *edited* record is a new logical
    // action; two different bodies under one key are IDEMPOTENCY_CONFLICT, not a retry
    (v) => actionIntent('PATCH', '/environments/update', v.environmentKey, hashJson(v.body)),
    (v) => [qk.environment(v.environmentKey), qk.environments],
  );
}

export function useRegisterProvider() {
  const qc = useQueryClient();
  return write<T.RegisterProviderRequest>(
    qc,
    'POST',
    () => '/governance/providers',
    (v) => v,
    (v) => actionIntent('POST', '/governance/providers', v.name, hashJson(v)),
    () => [qk.governanceProviders],
  );
}

/**
 * Store or rotate one credential (S5IF-SEC01). The value is the request body and nothing else:
 * `write()` invalidates `qk.secrets` (and, generically, `qk.setup`) on success but never caches the
 * response body, so the reference is held only by the form that asked for it.
 */
export function useSaveSecret() {
  const qc = useQueryClient();
  return write<{ name: string; material: string }>(
    qc,
    'PUT',
    (v) => `/secrets/${encodeURIComponent(v.name)}`,
    (v) => ({ material: v.material }),
    // No intent, so no `Idempotency-Key` — unlike every other write here.  S1 keeps `/secrets` out
    // of the replay table because that table stores response bodies verbatim, which for this route
    // would mean storing a credential reference; the header is therefore not read at all, and the
    // desktop client's generated types now reject sending it.  Repeatability comes from `PUT`.
    undefined,
    () => [qk.secrets],
  );
}

/** Remove a credential; 409 `SECRET_IN_USE` while an environment or provider still names it. */
export function useDeleteSecret() {
  const qc = useQueryClient();
  return write<{ name: string }>(
    qc,
    'DELETE',
    (v) => `/secrets/${encodeURIComponent(v.name)}`,
    undefined,
    undefined,
    () => [qk.secrets],
  );
}

export function useRegisterModel() {
  const qc = useQueryClient();
  return write<T.RegisterModelRequest>(
    qc,
    'POST',
    () => '/governance/models',
    (v) => v,
    (v) => actionIntent('POST', '/governance/models', `${v.provider}/${v.model}`, hashJson(v)),
    () => [qk.governanceModels],
  );
}

/**
 * Correct one model (S5IF-GOV06): the switch and the edit form share this hook because S1 gave
 * them one verb. The body names only what the operator touched; a field it omits keeps its stored
 * value, which is the whole difference between editing a model and re-registering it.
 *
 * The reference is `provider/model`, so it goes in the path verbatim.
 */
export function useUpdateModel() {
  const qc = useQueryClient();
  return write<{ ref: string; body: T.PatchModelRequest }>(
    qc,
    'PATCH',
    (v) => `/governance/models/${encodeURIComponent(v.ref)}`,
    (v) => v.body,
    (v) => actionIntent('PATCH', '/governance/models/{ref}', v.ref, hashJson(v.body)),
    () => [qk.governanceModels],
  );
}

/** Edit one provider; S1's PATCH leaves any field the body omits exactly as it was. */
export function useUpdateProvider() {
  const qc = useQueryClient();
  return write<{ name: string; body: T.PatchProviderRequest }>(
    qc,
    'PATCH',
    (v) => `/governance/providers/${encodeURIComponent(v.name)}`,
    (v) => v.body,
    (v) => actionIntent('PATCH', '/governance/providers/update', v.name, hashJson(v.body)),
    () => [qk.governanceProviders],
  );
}

/**
 * S5IF-GOV20 — one reachability probe of a provider's address.
 *
 * Nothing is invalidated: the probe reads the vendor, not the registration. The answer is shown
 * where it was asked, because an operator deciding whether an endpoint is wrong wants to see it next
 * to the address they just typed. A health probe belongs to no list cache, and a fresh key per tap is
 * what lets the same provider be tested twice in a row.
 */
export function useTestProvider() {
  const qc = useQueryClient();
  return write<{ name: string }>(
    qc,
    'POST',
    (v) => `/governance/providers/${encodeURIComponent(v.name)}/test-connection`,
    () => undefined,
    (v) => actionIntent('POST', '/governance/providers/test-connection', v.name, newUuid()),
    () => [],
  );
}

/**
 * Widen the organisation's data policy. The body is typed because S1 needs `change` — a sheet
 * that sent only `reason` was answered 422, which is how this button came to look broken.
 * The policy query is invalidated on success so the matrix on screen is the one just granted.
 */
export function useRelaxPolicy() {
  const qc = useQueryClient();
  return write<T.RelaxPolicyRequest>(
    qc,
    'POST',
    () => '/governance/policy/relaxations',
    (v) => v,
    (v) => actionIntent('POST', '/governance/policy/relaxations', String(v.change?.level ?? 'organization'), hashJson(v)),
    () => [qk.governancePolicy],
  );
}

export function useActivatePrompt() {
  const qc = useQueryClient();
  return write<unknown>(qc, 'POST', () => '/governance/prompts/activations', (v) => v, (v) => actionIntent('POST', '/governance/prompts/activate', JSON.stringify(v ?? '').slice(0, 24)), () => [qk.governancePrompts]);
}

// -------------------------------------------------------------------- settings
export function useSaveSystemSettings() {
  const qc = useQueryClient();
  return write<T.SystemSettingsBody>(qc, 'PUT', () => '/settings', (v) => v, () => actionIntent('PUT', '/settings', 'system'), () => [qk.settings]);
}

// -------------------------------------------------------------------- change requests
type CrStep = 'impact' | 'approve' | 'reject' | 'start' | 'complete' | 'cancel';
export function useCreateChangeRequest() {
  const qc = useQueryClient();
  return write<{ projectId: string; body: T.CreateChangeRequestBody }>(
    qc,
    'POST',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/change-requests`,
    (v) => v.body,
    (v) => actionIntent('POST', '/change-requests/create', v.projectId),
    (v) => [qk.changeRequests(v.projectId)],
  );
}

export function useChangeRequestStep() {
  const qc = useQueryClient();
  return write<{ crId: string; step: CrStep; body?: unknown; projectId?: string }>(
    qc,
    'POST',
    (v) => `/change-requests/${encodeURIComponent(v.crId)}/${v.step}`,
    (v) => v.body,
    // impact has no side effect; the rest are idempotent.
    (v) => (v.step === 'impact' ? undefined : actionIntent('POST', `/change-requests/${v.step}`, v.crId)),
    (v) => [qk.changeRequest(v.crId), ...(v.projectId ? [qk.changeRequests(v.projectId)] : [])],
  );
}

// ---------------------------------------------------------------- M2/M3 additions
/** Post-verify / manual-check reconciliation for an execution (UM-STG-003, UM-DEP-004). */
export function useReconcile() {
  const qc = useQueryClient();
  return write<string>(qc, 'POST', (id) => `/executions/${encodeURIComponent(id)}/reconcile`, () => ({}), (id) => actionIntent('POST', '/executions/reconcile', id), (id) => [['executions', id]]);
}

/** Delivery-mode upgrade (M3, UM-PRJ-003). */
export function useUpgradeMode() {
  const qc = useQueryClient();
  return write<{ projectId: string; workflowId?: string }>(
    qc,
    'POST',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/upgrade-mode`,
    () => ({}),
    (v) => actionIntent('POST', '/projects/upgrade-mode', v.projectId),
    (v) => [qk.project(v.projectId), qk.workflowOfProject(v.projectId), ...(v.workflowId ? [qk.stages(v.workflowId), qk.allowedActions(v.workflowId)] : [])],
  );
}

/** Resolve an unresolved environment binding to a chosen candidate uid (UM-DEP-004). */
export function useResolveBinding() {
  const qc = useQueryClient();
  return write<{ runId: string; businessKey: string; body: T.BindingSetRequest }>(
    qc,
    'PUT',
    (v) => `/deployment-runs/${encodeURIComponent(v.runId)}/environment-bindings/${encodeURIComponent(v.businessKey)}`,
    (v) => v.body,
    (v) => actionIntent('PUT', '/deployment-runs/binding', v.runId, v.businessKey),
    (v) => [qk.runBindings(v.runId), qk.runSummary(v.runId)],
  );
}

// -------------------------------------------------------------------- M4 auth & permissions
export function useLogin() {
  const qc = useQueryClient();
  return write<{ subject: string; password: string }>(qc, 'POST', () => '/auth/login', (v) => ({ subject: v.subject, password: v.password }), undefined, () => [['users']]);
}

export function useLogout() {
  const qc = useQueryClient();
  return write<undefined>(qc, 'POST', () => '/auth/logout', () => ({}), undefined, () => [['users']]);
}

export function useCreateUser() {
  const qc = useQueryClient();
  return write<T.UserCreateRequest>(qc, 'POST', () => '/users', (v) => v, (v) => actionIntent('POST', '/users', v.subject), () => [['users']]);
}

/**
 * One account's own facts: `displayName` is what people read, `status` is how an account is retired.
 *
 * S1 has no delete endpoint, so 停用 is the only way to take somebody out of the system, and a
 * display name typed at creation is a thing that needs correcting afterwards — both of them are
 * `PATCH /users/{id}` and neither was reachable from this console.
 */
export function usePatchUser() {
  const qc = useQueryClient();
  return write<{ userId: string; body: T.UserPatchRequest }>(
    qc,
    'PATCH',
    (v) => `/users/${encodeURIComponent(v.userId)}`,
    (v) => v.body,
    (v) => actionIntent('PATCH', '/users/{user_id}', v.userId, hashJson(v.body)),
    () => [['users']],
  );
}

export function useSetPassword() {
  const qc = useQueryClient();
  return write<{ userId: string; body: T.PasswordSetRequest }>(qc, 'POST', (v) => `/users/${encodeURIComponent(v.userId)}/password`, (v) => v.body, (v) => actionIntent('POST', '/users/password', v.userId), () => [['users']]);
}

export function useAssignRole() {
  const qc = useQueryClient();
  return write<{ userId: string; body: T.RoleAssignmentRequest }>(
    qc,
    'PUT',
    (v) => `/users/${encodeURIComponent(v.userId)}/roles`,
    (v) => v.body,
    (v) => actionIntent('PUT', '/users/roles', v.userId, v.body.role + (v.body.projectId ?? '')),
    (v) => [['users'], ['roles'], ...(v.body.projectId ? [qk.members(v.body.projectId)] : [])],
  );
}

// -------------------------------------------------------------------- M4 members / grants / exemptions
/**
 * FR-SEC-004: replace this project's whole membership.
 *
 * A PUT of the set, because that is the route S1 offers: a write that only added rows could never
 * remove one. The body therefore belongs in the intent (UI-REQ-001) — keyed on the route and the
 * project alone, a second edit of the same list read as a replay of the first and S1 answered with
 * the first response, leaving the operator looking at a membership they had not written.
 */
export function useReplaceMembers() {
  const qc = useQueryClient();
  return write<{ projectId: string; body: T.MemberPutRequest }>(
    qc,
    'PUT',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/members`,
    (v) => v.body,
    (v) => actionIntent('PUT', '/projects/{project_id}/members', v.projectId, payloadDigest(JSON.stringify(v.body))),
    (v) => [qk.members(v.projectId)],
  );
}

/** One member out. S1 names the account in the path and reads no `Idempotency-Key` on this route. */
export function useRemoveMember() {
  const qc = useQueryClient();
  return write<{ projectId: string; userId: string }>(
    qc,
    'DELETE',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/members/${encodeURIComponent(v.userId)}`,
    undefined,
    undefined,
    (v) => [qk.members(v.projectId)],
  );
}

/**
 * FR-SEC-003: authorise one person to read one classification. The body carries the digest because a
 * retried POST that minted a second grant would be a second authorisation on the record.
 *
 * The members list is invalidated as well: S1's `grant_restricted` calls `_ensure_membership` before
 * it writes the grant, so a grant for a stranger silently makes them a member of the project — a
 * members screen left on a stale cache would be showing a membership that has already happened.
 */
export function useAddGrant() {
  const qc = useQueryClient();
  return write<{ projectId: string; body: T.GrantCreateRequest }>(
    qc,
    'POST',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/artifact-grants`,
    (v) => v.body,
    (v) => actionIntent('POST', '/projects/{project_id}/artifact-grants', v.projectId, payloadDigest(JSON.stringify(v.body))),
    (v) => [qk.artifactGrants(v.projectId), qk.members(v.projectId)],
  );
}

/** Revoke one authorisation: the account is in the path, the classification in the query (both required). */
export function useRevokeGrant() {
  const qc = useQueryClient();
  return write<{ projectId: string; userId: string; classification: string }>(
    qc,
    'DELETE',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/artifact-grants/${encodeURIComponent(v.userId)}`,
    undefined,
    undefined,
    (v) => [qk.artifactGrants(v.projectId)],
    undefined,
    (v) => ({ classification: v.classification }),
  );
}

/** CP-WF-020: record a production waiver, reason and expiry included — the reason is what makes it auditable. */
export function useAddExemption() {
  const qc = useQueryClient();
  return write<{ projectId: string; body: T.ExemptionCreateRequest }>(
    qc,
    'POST',
    (v) => `/projects/${encodeURIComponent(v.projectId)}/production-exemptions`,
    (v) => v.body,
    (v) => actionIntent('POST', '/projects/{project_id}/production-exemptions', v.projectId, payloadDigest(JSON.stringify(v.body))),
    (v) => [qk.productionExemptions(v.projectId)],
  );
}

/**
 * Revoke a waiver. The route is top-level (`/production-exemptions/{id}`, not under the project) and
 * names its target in the path, so no idempotency key is sent; the project id travels only so the
 * right list is re-read afterwards.
 */
export function useRevokeExemption() {
  const qc = useQueryClient();
  return write<{ projectId: string; exemptionId: string }>(
    qc,
    'DELETE',
    (v) => `/production-exemptions/${encodeURIComponent(v.exemptionId)}`,
    undefined,
    undefined,
    (v) => [qk.productionExemptions(v.projectId)],
  );
}


export function useRevokeRole() {
  const qc = useQueryClient();
  return write<{ userId: string; role: string; projectId?: string }>(
    qc,
    'DELETE',
    (v) => `/users/${encodeURIComponent(v.userId)}/roles`,
    undefined,
    (v) => actionIntent('DELETE', '/users/roles/revoke', v.userId, v.role + (v.projectId ?? '')),
    () => [['users'], ['roles']],
    undefined,
    (v) => ({ role: v.role, projectId: v.projectId }),
  );
}

// -------------------------------------------------------------------- budget write
export function useSetBudget() {
  const qc = useQueryClient();
  return write<{ projectId?: string; body: T.BudgetSetRequest }>(
    qc,
    'PUT',
    () => '/governance/budget',
    (v) => v.body,
    (v) => actionIntent('PUT', '/governance/budget', v.projectId ?? 'platform', hashJson(v.body)),
    () => [['governance', 'budget']],
    undefined,
    (v) => ({ projectId: v.projectId }),
  );
}

// -------------------------------------------------------------------- M3 stage chat
export function useSendChat() {
  const qc = useQueryClient();
  return write<{ stageId: string; message: string }>(
    qc,
    'POST',
    (v) => `/stages/${encodeURIComponent(v.stageId)}/chat`,
    (v) => ({ message: v.message }),
    (v) => actionIntent('POST', '/stages/chat', v.stageId, hashJson({ message: v.message })),
    (v) => [['stages', v.stageId, 'chat']],
  );
}

export function useClearChat() {
  const qc = useQueryClient();
  return write<string>(qc, 'DELETE', (id) => `/stages/${encodeURIComponent(id)}/chat`, undefined, (id) => actionIntent('DELETE', '/stages/chat/clear', id), (id) => [['stages', id, 'chat']]);
}
