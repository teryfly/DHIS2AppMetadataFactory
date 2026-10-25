/*
 * State-changing operations. Each:
 *   - attaches X-Operator + Accept-Language via the client middleware (UI-OP-003, UI-APP-002),
 *   - supplies a stable Idempotency-Key per logical action (UI-REQ-001, UI-BR-04),
 *   - invalidates exactly the affected S1 reads on success (so the view refetches truth),
 *   - never auto-retries (UI-BR-05).
 * COMMIT gets a dedicated "result pending confirmation" path (UI-REQ-004): a network failure does
 * NOT resubmit; the user retries explicitly under the SAME key.
 */
import { useMutation, useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query';
import { client, unwrap, mutatingHeader } from './client';
import { uploadInput, type UploadArgs } from './upload';
import { qk } from './queryKeys';
import { isApiError } from './errors';
import { releaseIdempotencyKey, actionIntent, payloadDigest, newUuid } from './idempotency';
import { clearCsrfToken, useCsrfStore } from '@/stores/csrf';
import { useOperatorStore } from '@/stores/operator';
import type * as T from './types';

const P = '/api/v1';

/**
 * Whether a failed attempt still owns its idempotency key.
 *
 * A refusal S1 *answered* changed nothing, and the key is now spent: reusing it replays that same
 * refusal byte-for-byte, or, once the body is corrected, comes back 422 IDEMPOTENCY_CONFLICT
 * (CP-API-003). Either way the user cannot act again from this tab. So the key goes, and the next
 * click is a new request. Only an outcome we do not know keeps its key: a network failure (the
 * request may have landed) and REQUEST_IN_PROGRESS (the server is still working on that very
 * request, and a second key would run the action twice).
 */
export function keepsIdempotencyKey(error: unknown): boolean {
  if (!isApiError(error)) return true;
  return error.networkError || error.errorCode === 'REQUEST_IN_PROGRESS';
}

/** Small helper: run a mutation, invalidate `keys` on success, release the idempotency key. */
function useInvalidatingMutation<TVars, TResult>(opts: {
  mutationKey: readonly unknown[];
  /** A string, or the intent read off the variables when one call cannot stand for another. */
  intent?: string | ((vars: TVars) => string);
  mutationFn: (vars: TVars) => Promise<TResult>;
  invalidate?: (vars: TVars) => QueryKey[];
}) {
  const qc = useQueryClient();
  // Hooks that mint the key from their own variables (`mutatingHeader(intent)`) do not repeat
  // the intent string here: reading it back off the variables is what makes the release below
  // happen for them too. A hook whose intent is derived inside `mutationFn` from the request
  // body has no key worth releasing twice — a corrected body is already a different intent.
  const intentOf = (vars: TVars) =>
    typeof opts.intent === 'function'
      ? opts.intent(vars)
      : opts.intent ?? (typeof (vars as { intent?: unknown })?.intent === 'string'
        ? (vars as { intent: string }).intent
        : undefined);
  return useMutation<TResult, Error, TVars>({
    mutationKey: opts.mutationKey,
    mutationFn: opts.mutationFn,
    onSuccess: (_res, vars) => {
      for (const key of opts.invalidate?.(vars) ?? []) {
        void qc.invalidateQueries({ queryKey: key, exact: false });
      }
      const intent = intentOf(vars);
      if (intent) releaseIdempotencyKey(intent);
    },
    onError: (error, vars) => {
      if (keepsIdempotencyKey(error)) return;
      const intent = intentOf(vars);
      if (intent) releaseIdempotencyKey(intent);
    },
  });
}

export function useCreateProject() {
  return useInvalidatingMutation<{ body: T.CreateProjectBody }, T.ProjectView>({
    mutationKey: ['createProject'],
    intent: 'project.create',
    mutationFn: ({ body }) =>
      unwrap<T.ProjectView>(() => client.POST(`${P}/projects`, { body: body as never })),
    invalidate: () => [qk.projects, qk.setup],
  });
}

export function useAnswerQuestion(workflowId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['answerQuestion'],
      mutationFn: ({ questionId, body, intent }: { questionId: string; body: T.AnswerQuestionBody; intent: string }) => {
        return unwrap<T.QuestionView>(() =>
          client.POST(`${P}/questions/{question_id}/answer`, {
            params: { path: { question_id: questionId }, header: mutatingHeader(intent) },
            body: body as never,
          }),
        );
      },
      invalidate: () => [qk.questions(workflowId), qk.gates(workflowId), qk.allowedActions(workflowId), qk.stages(workflowId)],
    },
  );
}

/**
 * The review screen's one submit (CP-Q-005). The whole questionnaire goes in one request so the
 * run resumes once, after its last blocking answer, rather than continuing past a question the
 * reviewer had not reached yet. The body is part of the idempotency intent: a corrected
 * submission is a new action, not a replay of the refused one.
 */
export function useAnswerQuestions(workflowId: string) {
  return useInvalidatingMutation<
    { body: T.AnswerQuestionsBody; intent: string },
    T.QuestionnaireAnswerView
  >({
    mutationKey: ['answerQuestions'],
    mutationFn: ({ body, intent }) =>
      unwrap<T.QuestionnaireAnswerView>(() =>
        client.POST(`${P}/questions/answers`, {
          params: { header: mutatingHeader(intent) },
          body: body as never,
        }),
      ),
    invalidate: () => [
      qk.questions(workflowId),
      qk.gates(workflowId),
      qk.allowedActions(workflowId),
      qk.stages(workflowId),
    ],
  });
}

/**
 * UI-DEP-003 / S5IF-BP10: hand-set one environment binding. S1 takes the version and the
 * environment from the Run, so the body is the chosen uid plus the note S2 requires — the
 * reason a machine could not resolve it.  No idempotency key: the PUT is already one address
 * carrying one value (UI-BR-04's "where applicable" is S1's declared parameter list).
 */
export function useSetBinding(workflowId: string, runId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['setBinding', runId],
      mutationFn: ({
        businessKey,
        body,
      }: {
        businessKey: string;
        body: { uid: string; note: string };
      }) =>
        unwrap<T.BindingView>(() =>
          client.PUT(`${P}/deployment-runs/{run_id}/environment-bindings/{business_key}`, {
            params: { path: { run_id: runId, business_key: businessKey } },
            body: body as never,
          }),
        ),
      invalidate: () => [qk.bindings(runId), qk.run(runId), qk.runSummary(runId), qk.allowedActions(workflowId)],
    },
  );
}

export function useApproveGate(workflowId: string, projectId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['approveGate'],
      mutationFn: ({ gateId, body }: { gateId: string; body: T.ApproveGateBody }) =>
        unwrap<unknown>(() =>
          client.POST(`${P}/gates/{gate_id}/approve`, { params: { path: { gate_id: gateId } }, body: body as never }),
        ),
      invalidate: ({ gateId }) => [qk.gate(gateId), qk.gates(workflowId), qk.stages(workflowId), qk.allowedActions(workflowId), qk.workflow(projectId), qk.gatesPending],
    },
  );
}

export function useRejectGate(workflowId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['rejectGate'],
      mutationFn: ({ gateId, body }: { gateId: string; body: T.RejectGateBody }) =>
        unwrap<unknown>(() =>
          client.POST(`${P}/gates/{gate_id}/reject`, { params: { path: { gate_id: gateId } }, body: body as never }),
        ),
      invalidate: ({ gateId }) => [qk.gate(gateId), qk.gates(workflowId), qk.stages(workflowId), qk.allowedActions(workflowId), qk.notifications()],
    },
  );
}

export function useSubmitPatch(workflowId: string, projectId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['submitPatch'],
      mutationFn: ({ stageId, patch, intent }: { stageId: string; patch: Record<string, unknown>; intent: string }) =>
        unwrap<T.PatchSubmitView>(() =>
          client.POST(`${P}/stages/{stage_id}/patches`, {
            params: { path: { stage_id: stageId }, header: mutatingHeader(intent) },
            body: { patch } as never,
          }),
        ),
      invalidate: () => [qk.blueprintDraft(projectId), qk.stages(workflowId), qk.allowedActions(workflowId)],
    },
  );
}

export function useSubmitForApproval(workflowId: string, projectId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['submitForApproval'],
      mutationFn: ({ stageId }: { stageId: string }) =>
        unwrap<T.SubmitApprovalView>(() =>
          client.POST(`${P}/stages/{stage_id}/submit-for-approval`, { params: { path: { stage_id: stageId } } }),
        ),
      invalidate: () => [qk.stages(workflowId), qk.gates(workflowId), qk.allowedActions(workflowId), qk.workflow(projectId), qk.gatesPending],
    },
  );
}

export function useRunStage(workflowId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['runStage'],
      // S1 dropped Idempotency-Key from run/rerun (guarded by "already running"); do not send it.
      mutationFn: ({ stageId, rerun }: { stageId: string; rerun?: boolean }) =>
        unwrap<unknown>(() =>
          rerun
            ? client.POST(`${P}/stages/{stage_id}/rerun`, { params: { path: { stage_id: stageId } } })
            : client.POST(`${P}/stages/{stage_id}/run`, { params: { path: { stage_id: stageId } } }),
        ),
      invalidate: (vars) => [qk.stages(workflowId), qk.stage(vars.stageId), qk.allowedActions(workflowId), qk.workflowArtifacts(workflowId)],
    },
  );
}

export function useResolveManualCheck(workflowId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['resolveManualCheck'],
      mutationFn: ({ stageId, note, outcome }: { stageId: string; note: string; outcome: 'PASSED' | 'FAILED' }) =>
        unwrap<T.ManualCheckView>(() =>
          client.POST(`${P}/stages/{stage_id}/resolve-manual-check`, {
            params: { path: { stage_id: stageId } },
            body: { note, outcome } as never,
          }),
        ),
      invalidate: () => [qk.stages(workflowId), qk.allowedActions(workflowId), qk.workflowArtifacts(workflowId)],
    },
  );
}

export function useWorkflowFlag(workflowId: string, projectId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['workflowFlag'],
      mutationFn: ({ flag }: { flag: 'pause' | 'resume' | 'cancel' }) => {
        const call = () =>
          flag === 'pause'
            ? client.POST(`${P}/workflows/{workflow_id}/pause`, { params: { path: { workflow_id: workflowId } } })
            : flag === 'resume'
              ? client.POST(`${P}/workflows/{workflow_id}/resume`, { params: { path: { workflow_id: workflowId } } })
              : client.POST(`${P}/workflows/{workflow_id}/cancel`, { params: { path: { workflow_id: workflowId } } });
        return unwrap<unknown>(call);
      },
      invalidate: () => [qk.workflow(projectId), qk.allowedActions(workflowId), qk.stages(workflowId)],
    },
  );
}

export function useMarkNotificationRead() {
  return useInvalidatingMutation(
    {
      mutationKey: ['markNotificationRead'],
      mutationFn: ({ notificationId }: { notificationId: string }) =>
        unwrap<unknown>(() =>
          client.POST(`${P}/notifications/{notification_id}/read`, { params: { path: { notification_id: notificationId } } }),
        ),
      invalidate: () => [qk.notifications()],
    },
  );
}

/**
 * COMMIT (UI-REQ-004 / UI-BR-05). On a network failure the result is UNKNOWN and must NOT be
 * auto-resubmitted; we surface `pending` so the console keeps the same idempotency key for an
 * explicit user retry. Any other error (409, locked, outside window) is thrown normally.
 */
export function useCommitRun(workflowId: string, runId: string, projectId: string) {
  const qc = useQueryClient();
  return useMutation<unknown, Error, { intent: string }>({
    mutationKey: ['commitRun', runId],
    mutationFn: ({ intent }) =>
      unwrap<unknown>(() =>
        client.POST(`${P}/deployment-runs/{run_id}/commit`, {
          params: { path: { run_id: runId }, header: mutatingHeader(intent) },
        }),
      ),
    onSuccess: () => {
      releaseIdempotencyKey(`commit:run:${runId}`);
      for (const key of [qk.run(runId), qk.runSummary(runId), qk.deploymentRuns(workflowId), qk.allowedActions(workflowId), qk.workflow(projectId)]) {
        void qc.invalidateQueries({ queryKey: key, exact: false });
      }
    },
    onError: (error) => {
      // Preserve the key when the outcome is unknown or still being worked out (UI-AC-07,
      // CP-GATE-018: the console must never start a second COMMIT under a fresh key).
      if (keepsIdempotencyKey(error)) return;
      releaseIdempotencyKey(`commit:run:${runId}`);
    },
  });
}

export function useCreateRun(workflowId: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['createRun'],
      mutationFn: ({ body, intent }: { body: { environmentKey: string }; intent: string }) =>
        unwrap<unknown>(() =>
          client.POST(`${P}/workflows/{workflow_id}/deployment-runs`, {
            params: { path: { workflow_id: workflowId }, header: mutatingHeader(intent) },
            body: body as never,
          }),
        ),
      invalidate: () => [qk.deploymentRuns(workflowId), qk.allowedActions(workflowId)],
    },
  );
}

export function useSaveSettings() {
  return useInvalidatingMutation(
    {
      mutationKey: ['saveSettings'],
      mutationFn: ({ body }: { body: Record<string, unknown> }) =>
        unwrap<unknown>(() => client.PUT(`${P}/settings`, { body: body as never })),
      invalidate: () => [qk.systemSettings],
    },
  );
}

/**
 * S1 declares this route `idempotent`, so the header has to be there — and it has to be a *new*
 * key on every press, which is the one place a minted-and-stored intent would be wrong: a
 * repeated key replays the stored answer, and "test the connection again" that shows the previous
 * result is worse than no button. A retry of one click is a manual second click, and a fresh
 * probe is exactly what the operator asked for.
 */
export function useTestConnection() {
  return useInvalidatingMutation({
    mutationKey: ['testConnection'],
    mutationFn: ({ environmentKey, requiredScopes }: { environmentKey: string; requiredScopes?: string[] }) =>
      unwrap<unknown>(() =>
        client.POST(`${P}/environments/{environment_key}/test-connection`, {
          params: {
            path: { environment_key: environmentKey },
            header: { 'Idempotency-Key': newUuid() },
          },
          // S1 defaults these to discovery + import; naming them keeps the answer stable and
          // is what makes the import-scope fact get persisted (DX-EXE-012).
          body: { requiredScopes } as never,
        }),
      ),
    // A passing test is what removes an environment from `setup.environments.neverTested`, so the
    // getting-started checklist must be re-read or it keeps reporting a tested row as untested.
    invalidate: ({ environmentKey }: { environmentKey: string }) => [qk.connectionTest(environmentKey), qk.environment(environmentKey), qk.setup],
  });
}

/**
 * S1 requires a reason when locking (`EmergencyLockRequest`) and none when releasing it, and
 * reads the flag as `locked` — an earlier body sent `lock`, which its camelCase model ignored.
 */
export function useEmergencyLock() {
  return useInvalidatingMutation({
    mutationKey: ['emergencyLock'],
    mutationFn: ({ environmentKey, locked, reason }: { environmentKey: string; locked: boolean; reason?: string }) =>
      unwrap<unknown>(() =>
        client.POST(`${P}/environments/{environment_key}/emergency-lock`, {
          params: { path: { environment_key: environmentKey } },
          body: { locked, reason } as never,
        }),
      ),
    invalidate: ({ environmentKey }: { environmentKey: string }) => [
      qk.environments,
      qk.environment(environmentKey),
      // the lock is a column of the protection row, which is where the page reads it from
      qk.environmentProtection(environmentKey),
    ],
  });
}

/**
 * Switch an environment out of service. S4 keeps the row and refuses every operation against
 * it, which is why the action list closes test/probe/disable afterwards — so the reason is
 * optional here but worth asking for: it is the only trace of why an environment went quiet.
 */
export function useDisableEnvironment() {
  return useInvalidatingMutation<{ environmentKey: string; reason?: string }, unknown>({
    mutationKey: ['disableEnvironment'],
    mutationFn: ({ environmentKey, reason }) => {
      const intent = actionIntent('POST', '/environments/{environment_key}/disable', environmentKey, payloadDigest(reason ?? ''));
      return unwrap(() =>
        client.POST(`${P}/environments/{environment_key}/disable`, {
          params: { path: { environment_key: environmentKey }, header: mutatingHeader(intent) },
          body: { reason } as never,
        }),
      );
    },
    invalidate: ({ environmentKey }) => [qk.environments, qk.environment(environmentKey)],
  });
}

/**
 * Ask the instance what it is, and store the answer as a capability profile.
 *
 * The route is declared `idempotent`, and a repeated key would replay the previous probe — so
 * each press carries a fresh one, the same way a connection test does.
 */
export function useProbeCapabilities() {
  return useInvalidatingMutation<{ environmentKey: string }, T.ProbeResultView>({
    mutationKey: ['probeCapabilities'],
    mutationFn: ({ environmentKey }) =>
      unwrap<T.ProbeResultView>(() =>
        client.POST(`${P}/environments/{environment_key}/probe-capabilities`, {
          params: { path: { environment_key: environmentKey }, header: { 'Idempotency-Key': newUuid() } },
        }),
      ),
    invalidate: ({ environmentKey }) => [
      qk.capabilityProfile(environmentKey),
      qk.environment(environmentKey),
      qk.environments,
    ],
  });
}

/**
 * Create or edit one environment.
 *
 * The intent folds the submitted body into the key: a create that failed and is then retried
 * *after an edit* is a different logical action, and reusing one key for two bodies is
 * IDEMPOTENCY_CONFLICT — an error the operator cannot interpret.
 */
export function useCreateEnvironment() {
  return useInvalidatingMutation<T.CreateEnvironmentRequest, T.EnvironmentView>({
    mutationKey: ['createEnvironment'],
    mutationFn: (body) => {
      const intent = actionIntent('POST', '/environments', body.environmentKey, payloadDigest(JSON.stringify(body)));
      return unwrap<T.EnvironmentView>(() =>
        client.POST(`${P}/environments`, { params: { header: mutatingHeader(intent) }, body: body as never }),
      );
    },
    invalidate: (body) => [qk.environments, qk.environmentProtection(body.environmentKey), qk.setup],
  });
}

/** The key is fixed for the life of the form, so the hook takes it like `usePatchUser` does. */
export function useUpdateEnvironment(environmentKey: string) {
  return useInvalidatingMutation<Partial<T.UpdateEnvironmentRequest>, T.EnvironmentView>({
    mutationKey: ['updateEnvironment', environmentKey],
    mutationFn: (body) => {
      const intent = actionIntent(
        'PATCH',
        '/environments/{environment_key}',
        environmentKey,
        payloadDigest(JSON.stringify(body)),
      );
      return unwrap<T.EnvironmentView>(() =>
        client.PATCH(`${P}/environments/{environment_key}`, {
          params: { path: { environment_key: environmentKey }, header: mutatingHeader(intent) },
          body: body as never,
        }),
      );
    },
    invalidate: () => [qk.environments, qk.environment(environmentKey), qk.environmentProtection(environmentKey), qk.setup],
  });
}

export function useRegisterProvider() {
  return useInvalidatingMutation<T.RegisterProviderRequest, T.ProviderView>({
    mutationKey: ['registerProvider'],
    mutationFn: (body) => {
      const intent = actionIntent('POST', '/governance/providers', body.name, payloadDigest(JSON.stringify(body)));
      return unwrap<T.ProviderView>(() =>
        client.POST(`${P}/governance/providers`, { params: { header: mutatingHeader(intent) }, body: body as never }),
      );
    },
    invalidate: () => [qk.governanceProviders, qk.setup],
  });
}

/**
 * `POST /governance/models` (S5IF-GOV05). The address and the key reference belong to the provider:
 * S1 joins them onto the model row when reading, so a model that carried its own endpoint would
 * keep using a stale address after the provider moved.
 */
export function useRegisterModel() {
  return useInvalidatingMutation<T.RegisterModelRequest, T.ModelView>({
    mutationKey: ['registerModel'],
    mutationFn: (body) => {
      const intent = actionIntent('POST', '/governance/models', `${body.provider}/${body.model}`, payloadDigest(JSON.stringify(body)));
      return unwrap<T.ModelView>(() =>
        client.POST(`${P}/governance/models`, { params: { header: mutatingHeader(intent) }, body: body as never }),
      );
    },
    invalidate: () => [qk.governanceModels, qk.setup],
  });
}

/**
 * Correct one model by its `provider/model` reference (S5IF-GOV06).
 *
 * One hook for both the switch and the edit form, because S1 gave them one verb: the body carries
 * only the fields the operator touched, and a field it omits keeps its stored value.  A separate
 * "enable" hook would have been a second spelling of the same request, and the second one is
 * always the one that drifts.
 */
export function useUpdateModel() {
  return useInvalidatingMutation<{ ref: string; body: T.PatchModelRequest }, T.ModelView>({
    mutationKey: ['updateModel'],
    mutationFn: ({ ref, body }) =>
      unwrap<T.ModelView>(() =>
        client.PATCH(`${P}/governance/models/{ref}`, {
          params: {
            path: { ref },
            header: mutatingHeader(
              actionIntent('PATCH', '/governance/models/{ref}', ref, payloadDigest(JSON.stringify(body))),
            ),
          },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.governanceModels, qk.setup],
  });
}

/** Edit one provider in place; the body carries only the fields the operator touched. */
export function useUpdateProvider() {
  return useInvalidatingMutation<{ name: string; body: Partial<T.PatchProviderRequest> }, T.ProviderView>({
    mutationKey: ['updateProvider'],
    // the name is a variable, not a hook argument: a table row toggles providers other than the
    // one the edit form has open, and an empty path segment would aim at `/providers/`.
    mutationFn: ({ name, body }) => {
      const intent = actionIntent('PATCH', '/governance/providers/{name}', name, payloadDigest(JSON.stringify(body)));
      return unwrap<T.ProviderView>(() =>
        client.PATCH(`${P}/governance/providers/{name}`, {
          params: { path: { name }, header: mutatingHeader(intent) },
          body: body as never,
        }),
      );
    },
    invalidate: () => [qk.governanceProviders, qk.setup],
  });
}

/**
 * S5IF-GOV20 — ask one provider's address whether it answers.
 *
 * Nothing is invalidated: the probe reads the vendor, not the registration, and the answer belongs
 * next to the row it was asked from. A fresh key per press is deliberate — a health probe is not an
 * action an operator could meaningfully "retry the same one", and reusing a spent key would make the
 * second press replay the first answer.
 */
export function useTestProvider() {
  return useInvalidatingMutation<{ name: string }, T.ProviderHealthView>({
    mutationKey: ['testProvider'],
    mutationFn: ({ name }) => {
      const intent = actionIntent('POST', '/governance/providers/{name}/test-connection', name, newUuid());
      return unwrap<T.ProviderHealthView>(() =>
        client.POST(`${P}/governance/providers/{name}/test-connection`, {
          params: { path: { name }, header: mutatingHeader(intent) },
        }),
      );
    },
    invalidate: () => [],
  });
}

/**
 * Widen the organisation's data policy, and read the matrix back.
 *
 * The invalidation is the point: a relaxation is permanent and org-wide, so the operator has to
 * see the matrix change in the same screen that asked for it. A relaxation that appears granted
 * but is not visible one request later is the failure this flow exists not to have.
 */
export function useRelaxPolicy() {
  return useInvalidatingMutation<T.RelaxPolicyRequest, T.RelaxationView>({
    mutationKey: ['relaxPolicy'],
    mutationFn: (body) => {
      const level = String(body.change?.level ?? 'organization');
      const intent = actionIntent('POST', '/governance/policy/relaxations', level, payloadDigest(JSON.stringify(body)));
      return unwrap<T.RelaxationView>(() =>
        client.POST(`${P}/governance/policy/relaxations`, { params: { header: mutatingHeader(intent) }, body: body as never }),
      );
    },
    invalidate: () => [qk.governancePolicy],
  });
}

/**
 * Project lifecycle (FR-DATA-006, CP-PRJ-008).  Archive, export and delete are wired here so
 * that the buttons in Project Settings do what S1 says they may do, and nothing more: whether
 * a project may be deleted (only when archived) is S1's rule and comes back through
 * allowed-actions, never inferred from the project row in the browser (FR-UI-009, UI-BR-01).
 */
export function useArchiveProject(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['archiveProject'],
    mutationFn: () =>
      unwrap<T.ProjectView>(() =>
        client.POST(`${P}/projects/{project_id}/archive`, {
          params: { path: { project_id: projectId } },
        }),
      ),
    invalidate: () => [qk.project(projectId), qk.projects],
  });
}

export function useExportProject(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['exportProject'],
    mutationFn: () =>
      unwrap<T.Acceptance>(() =>
        client.GET(`${P}/projects/{project_id}/export`, {
          params: { path: { project_id: projectId } },
        }),
      ),
    // the export lands as a PROJECT_EXPORT artifact; the project read is what
    // tells S5 an export was taken, and the artifact itself is polled by id
    invalidate: () => [qk.project(projectId)],
  });
}

export function useDeleteProject(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['deleteProject'],
    mutationFn: () =>
      unwrap<unknown>(() =>
        client.DELETE(`${P}/projects/{project_id}`, {
          params: { path: { project_id: projectId } },
        }),
      ),
    invalidate: () => [qk.projects],
  });
}

/**
 * UI-REQS-001: upload a business input with its declared classification (FR-REQ-001).
 *
 * The multipart call is in `upload.ts`, not here, because the typed client cannot report upload
 * progress; the invalidation rules are the same as any other mutation. There is no
 * Idempotency-Key: S1's upload route does not declare one, and a duplicate upload of the same
 * bytes is refused by the content hash rather than by a key.
 */
export function useUploadInput(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['uploadInput'],
    mutationFn: (args: UploadArgs) => uploadInput(args),
    invalidate: () => [qk.inputs(projectId), qk.uploadLimits],
  });
}

/**
 * S5IF-INP01 / S5IF-INP04: parse an input, or re-parse it with a confirmed column mapping.
 * One endpoint covers both (AI-INP-006); the caller passes the mapping it confirmed, and the
 * intent carries a digest of that mapping so confirming a *different* mapping is a new action
 * while clicking the same confirmation twice replays the first one (UI-REQ-001).
 */
export function useParseInput(projectId: string, workflowId: string) {
  return useInvalidatingMutation({
    mutationKey: ['parseInput'],
    mutationFn: ({ artifactId, options, intent }: { artifactId: string; options?: T.ParseOptions; intent: string }) =>
      unwrap<T.Acceptance>(() =>
        client.POST(`${P}/projects/{project_id}/inputs/{artifact_id}/parse`, {
          params: { path: { project_id: projectId, artifact_id: artifactId }, header: mutatingHeader(intent) },
          body: (options ? { options } : {}) as never,
        }),
      ),
    invalidate: ({ artifactId }: { artifactId: string }) => [
      qk.inputs(projectId),
      qk.columnMapping(projectId, artifactId),
      qk.questions(workflowId),
      qk.stages(workflowId),
      qk.nrr(workflowId),
    ],
  });
}

/**
 * S5IF-INP05: ask S3 to explain a report. The answer is an artifact id, never the text
 * (the body is read through the artifact-content endpoint), and null means the stage is not
 * enabled for explanation — which the UI must be able to show as "no explanation available".
 * No Idempotency-Key: the route declares X-Operator only, so a second click is a second
 * explanation rather than a replay, and inventing a header S1 does not accept would be a
 * request the server is entitled to ignore.
 */
export function useExplainArtifact(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['explainArtifact'],
    mutationFn: ({ artifactId }: { artifactId: string }) =>
      unwrap<T.ExplainResultView>(() =>
        client.POST(`${P}/artifacts/{artifact_id}/explain`, {
          params: { path: { artifact_id: artifactId } },
          body: { projectId } as never,
        }),
      ),
  });
}

/**
 * UI-REV-001: re-run the deterministic compiler for one artifact and compare hashes. No
 * Idempotency-Key — reproducing is idempotent by construction (S2's compilers take no clock,
 * random or LLM), and the answer is the comparison, not a new resource.
 */
export function useReproduceArtifact() {
  return useInvalidatingMutation({
    mutationKey: ['reproduceArtifact'],
    mutationFn: ({ artifactId }: { artifactId: string }) =>
      unwrap<T.ReproduceView>(() =>
        client.POST(`${P}/artifacts/{artifact_id}/reproduce`, { params: { path: { artifact_id: artifactId } } }),
      ),
  });
}

/**
 * UI-OPS-001: a change request, from reason to completed step.
 *
 * `impact` carries no Idempotency-Key because S1's route declares none; the lifecycle steps do,
 * because approving the same request twice must not be two approvals (CP-API-003).
 */
export type ChangeRequestStep = 'impact' | 'approve' | 'reject' | 'start' | 'complete' | 'cancel';

export function useCreateChangeRequest(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['createChangeRequest'],
    // S1 declares neither X-Operator nor an idempotency key on this route (see the contract gap
    // noted in the audit): the client middleware still attaches the operator, and inventing a
    // key the route does not accept would be a header the server is free to ignore.
    mutationFn: ({ body }: { body: T.CreateChangeRequestBody }) =>
      unwrap<T.ChangeRequestView>(() =>
        client.POST(`${P}/projects/{project_id}/change-requests`, {
          params: { path: { project_id: projectId } },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.changeRequests(projectId)],
  });
}

/**
 * One step of a change request's life (UI-OPS-001). Each branch is written out rather than
 * assembled from the step name: the routes differ in whether S1 asks for an Idempotency-Key,
 * and a generated string would quietly send a body or a header the contract does not declare.
 */
export function useChangeRequestStep(projectId: string) {
  return useInvalidatingMutation({
    mutationKey: ['changeRequestStep'],
    mutationFn: ({ crId, step, intent }: { crId: string; step: ChangeRequestStep; intent: string }) => {
      const path = { cr_id: crId };
      const key = { header: mutatingHeader(intent) };
      const call =
        step === 'impact'
          ? () => client.POST(`${P}/change-requests/{cr_id}/impact`, { params: { path } })
          : step === 'approve'
            ? () => client.POST(`${P}/change-requests/{cr_id}/approve`, { params: { path, ...key } })
            : step === 'reject'
              ? () =>
                  client.POST(`${P}/change-requests/{cr_id}/reject`, {
                    params: { path, ...key },
                    body: { reason: 'Rejected from the workbench' } as never,
                  })
              : step === 'start'
                ? () => client.POST(`${P}/change-requests/{cr_id}/start`, { params: { path, ...key } })
                : step === 'complete'
                  ? () => client.POST(`${P}/change-requests/{cr_id}/complete`, { params: { path, ...key } })
                  : () => client.POST(`${P}/change-requests/{cr_id}/cancel`, { params: { path, ...key } });
      return unwrap<Record<string, unknown>>(call);
    },
    invalidate: () => [qk.changeRequests(projectId)],
  });
}

/** S5IF-INP07 / UI-WB-009: stop an agent run that is still working. */
export function useCancelAgentRun(workflowId: string) {
  return useInvalidatingMutation({
    mutationKey: ['cancelAgentRun'],
    mutationFn: ({ runId }: { runId: string }) =>
      unwrap<unknown>(() =>
        client.POST(`${P}/workflows/{workflow_id}/agent-runs/{run_id}/cancel`, {
          params: { path: { workflow_id: workflowId, run_id: runId } },
        }),
      ),
    invalidate: ({ runId }: { runId: string }) => [qk.agentRun(workflowId, runId), qk.stages(workflowId)],
  });
}

/**
 * Stop a deployment run that has not committed. S1's own guard is that a run in COMMITTING can
 * not be cancelled out from under the instance, so a refusal here is information, not a bug.
 */
export function useCancelDeploymentRun(workflowId: string) {
  return useInvalidatingMutation({
    mutationKey: ['cancelDeploymentRun'],
    mutationFn: ({ runId }: { runId: string }) =>
      unwrap<unknown>(() =>
        client.POST(`${P}/deployment-runs/{run_id}/cancel`, {
          // one key per run: a second press of the same cancel replays S1's first answer
          // instead of asking it to cancel a run it already cancelled.
          params: { path: { run_id: runId }, header: mutatingHeader(actionIntent('POST', '/deployment-runs/{run_id}/cancel', runId)) },
        }),
      ),
    // the run list and the workflow's action list both change: an open run is what several
    // other controls are gated on
    invalidate: ({ runId }: { runId: string }) => [
      qk.run(runId),
      qk.runSummary(runId),
      qk.deploymentRuns(workflowId),
      qk.allowedActions(workflowId),
    ],
  });
}

// --- M4 session (UI-AUTH-001) -------------------------------------------------------

/**
 * Sign in.  Once a session exists S1 takes the identity from it and ignores the declared name
 * (CP-IAM-003); the client still sends `X-Operator` on every POST, and before there is a session
 * the subject being signed in for is the only name available, so the form's own subject is sent.
 * The CSRF value the next write needs is handed over here and nowhere else (CP-IAM-004).
 */
export function useSignIn() {
  const qc = useQueryClient();
  return useMutation<T.SessionView, Error, T.LoginBody>({
    mutationKey: ['signIn'],
    mutationFn: (body) =>
      unwrap<T.SessionView>(() =>
        client.POST(`${P}/auth/login`, {
          params: { header: { 'X-Operator': body.subject } },
          body: body as never,
        }),
      ),
    onSuccess: (view) => {
      if (view.csrfToken) useCsrfStore.getState().setToken(view.csrfToken);
      // The session subject becomes the operator name so audit and UI agree (UI-OP-003); the
      // declared header no longer decides identity, but it must not contradict the session either.
      if (view.subject) useOperatorStore.getState().setName(view.subject);
      qc.setQueryData(qk.session, view);
    },
  });
}

/**
 * UI-AUTH-001: trade the authorization code the organisation returned for a session.  The state
 * and nonce ride an HttpOnly cookie S1 set on the request, so the browser carries them and this
 * client never handles them — which is what makes a forged callback fail.
 */
export function useOidcExchange() {
  const qc = useQueryClient();
  return useMutation<T.SessionView, Error, { code: string; redirectUri: string }>({
    mutationKey: ['oidcExchange'],
    mutationFn: ({ code, redirectUri }) =>
      unwrap<T.SessionView>(() =>
        client.POST(`${P}/auth/oidc/exchange`, { body: { code, redirectUri } as never }),
      ),
    onSuccess: (view) => {
      if (view.csrfToken) useCsrfStore.getState().setToken(view.csrfToken);
      if (view.subject) useOperatorStore.getState().setName(view.subject);
      qc.setQueryData(qk.session, view);
    },
  });
}

/**
 * UI-AUTH-001: end the session and forget the CSRF value with it.  No idempotency key: a second
 * logout must be free to answer again after a new sign-in, and ending a session is already its own
 * once-only act on the server.
 */
/**
 * Forget every local trace of a session that has ended (UI-AUTH-001).
 *
 * Two things end a session without this app choosing it: signing out, and changing your own
 * password — S1 revokes the account's live sessions when the digest changes
 * (`identity/service.py` → `sessions.revoke_for_user`), so the cookie in the browser is already
 * dead. Keeping the cached session answer and the mirrored CSRF value would leave the page
 * believing it still knows who is here, until a write fails for a reason nobody can name.
 */
export function dropSession(qc: QueryClient): void {
  clearCsrfToken();
  // A person who signed themselves out has not had their session lapse; without clearing the flag
  // the login screen greeted them with "your session expired".
  useCsrfStore.getState().clearLapsed();
  qc.removeQueries({ queryKey: qk.session });
  void qc.invalidateQueries();
}

export function useSignOut() {
  const qc = useQueryClient();
  return useMutation<T.ClearedView, Error, void>({
    mutationKey: ['signOut'],
    mutationFn: () => unwrap<T.ClearedView>(() => client.POST(`${P}/auth/logout`, {})),
    onSuccess: () => dropSession(qc),
  });
}

// --- budget (AI-COST-002/003, UI-ADM-005) -------------------------------------------

/**
 * Write the project's limit and warning thresholds.  `limit: null` clears it; the response carries
 * S3's own `state`, which is what the screen shows — a local comparison would disagree with the
 * rule that refuses runs (AI-COST-003).
 */
export function useSetBudget(projectId: string) {
  const intent = actionIntent('PUT', '/governance/budget', projectId);
  return useInvalidatingMutation<T.BudgetSetRequest, T.BudgetView>({
    mutationKey: ['setBudget', projectId],
    intent,
    mutationFn: (body) =>
      unwrap<T.BudgetView>(() =>
        client.PUT(`${P}/governance/budget`, {
          params: { query: { projectId }, header: mutatingHeader(intent) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.governanceBudget(projectId), qk.governanceUsage(projectId)],
  });
}

/**
 * UI-ADM-003 / S5IF-GOV09: make one prompt version effective for an agent.
 *
 * The body is part of the intent: activating v4 of one prompt and v4 of another are two different
 * decisions, and a key that named only the route made the second one read as a replay of the first.
 */
export function useActivatePrompt() {
  const intentOf = (body: T.ActivatePromptRequest) =>
    actionIntent('POST', '/governance/prompts/activations', body.agentType, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<T.ActivatePromptRequest, unknown>({
    mutationKey: ['activatePrompt'],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<unknown>(() =>
        client.POST(`${P}/governance/prompts/activations`, {
          params: { header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.governancePrompts, qk.platformAllowedActions],
  });
}

// --- in-stage dialogue (AI-CHAT-001~003, UI-WB-010) ----------------------------------

/**
 * One turn of the stage's conversation.  The intent carries a digest of the message, so a client
 * that retries a turn it did not receive replays the same answer while a new message is a new
 * action (UI-REQ-001) — S1's own default key is per stage+operator, which would fold two different
 * questions into one.  A turn can write patches, so the stage and its draft are refetched too.
 */
export function useChatTurn(stageId: string, workflowId?: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['chatTurn', stageId],
      mutationFn: ({ message }: { message: string }) => {
        const intent = actionIntent('POST', '/stages/{stage_id}/chat', stageId, payloadDigest(message));
        return unwrap<T.ChatTurnView>(() =>
          client.POST(`${P}/stages/{stage_id}/chat`, {
            params: { path: { stage_id: stageId }, header: mutatingHeader(intent) },
            body: { message } as never,
          }),
        );
      },
      invalidate: () => [
        qk.stageChat(stageId),
        qk.stage(stageId),
        ...(workflowId ? [qk.stages(workflowId), qk.allowedActions(workflowId)] : []),
      ],
    },
  );
}

/** AI-CHAT-003: clear the session S3 holds for this stage. */
export function useClearChat(stageId: string, workflowId?: string) {
  return useInvalidatingMutation(
    {
      mutationKey: ['clearChat', stageId],
      mutationFn: () =>
        unwrap<T.ChatClearedView>(() =>
          client.DELETE(`${P}/stages/{stage_id}/chat`, { params: { path: { stage_id: stageId } } }),
        ),
      invalidate: () => [qk.stageChat(stageId), ...(workflowId ? [qk.stages(workflowId)] : [])],
    },
  );
}

// --- M4 administration (CP-IAM-002, FR-SEC-002/003/004, UI-AUTH-003) ----------------

/**
 * The write operations behind the Administration screens.  Each is gated by the action S1 itself
 * checks on that route (`user.manage` et al.), and each declares an idempotency key only where the
 * contract declares the parameter — a DELETE whose target is named in the path is already
 * replay-safe (UI-BR-04).
 */
export function useCreateUser() {
  const intent = actionIntent('POST', '/users');
  return useInvalidatingMutation<Record<string, unknown>, T.UserView>({
    mutationKey: ['createUser'],
    intent,
    mutationFn: (body) =>
      unwrap<T.UserView>(() =>
        client.POST(`${P}/users`, { params: { header: mutatingHeader(intent) }, body: body as never }),
      ),
    invalidate: () => [qk.users],
  });
}

/**
 * Correct one account: its display name, its address, or whether it is live at all.
 *
 * The id arrives with the call rather than being bound when the hook is created, because the users
 * table has one button per row: `usePatchUser('')` built every request against an empty id and the
 * screen's 启用/停用 therefore asked S1 for a route that does not exist. The body also belongs in the
 * intent — renaming and retiring the same account are two different actions, and one intent would
 * make the second a replay of the first.
 */
const userPatchIntent = (userId: string, body: Record<string, unknown>) =>
  actionIntent('PATCH', '/users/{user_id}', userId, payloadDigest(JSON.stringify(body)));

export function usePatchUser() {
  return useInvalidatingMutation<{ userId: string; body: Record<string, unknown> }, T.UserView>({
    mutationKey: ['patchUser'],
    intent: ({ userId, body }) => userPatchIntent(userId, body),
    mutationFn: ({ userId, body }) =>
      unwrap<T.UserView>(() => {
        const intent = userPatchIntent(userId, body);
        return client.PATCH(`${P}/users/{user_id}`, {
          params: { path: { user_id: userId }, header: mutatingHeader(intent) },
          body: body as never,
        });
      }),
    invalidate: () => [qk.users],
  });
}

/** FR-SEC-001: a password is written once and never read back; the value never lands in a store. */
export function useSetUserPassword(userId: string) {
  return useMutation<T.ClearedView, Error, { password: string; mustChangePassword?: boolean }>({
    mutationKey: ['setUserPassword', userId],
    mutationFn: (body) =>
      unwrap<T.ClearedView>(() =>
        client.POST(`${P}/users/{user_id}/password`, {
          params: { path: { user_id: userId } },
          body: body as never,
        }),
      ),
  });
}

export function useAssignRole(userId: string) {
  /**
   * The intent names the *grant*, not just the account. Keying it on the user alone makes two
   * different roles for the same person the same action, so the second grant would be answered with
   * the first one's stored replay (CP-API-003) — a role that silently never landed is exactly the
   * kind of thing nobody notices until somebody cannot approve a Gate.
   */
  const intentOf = (body: { role: string; projectId?: string }) =>
    actionIntent('PUT', '/users/{user_id}/roles', userId, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<{ role: string; projectId?: string }, T.RoleAssignmentView>({
    mutationKey: ['assignRole', userId],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<T.RoleAssignmentView>(() =>
        client.PUT(`${P}/users/{user_id}/roles`, {
          params: { path: { user_id: userId }, header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.users, qk.roles],
  });
}

export function useRevokeRole(userId: string) {
  return useInvalidatingMutation<{ role: string; projectId?: string }, T.RoleAssignmentView>({
    mutationKey: ['revokeRole', userId],
    mutationFn: ({ role, projectId }) =>
      unwrap<T.RoleAssignmentView>(() =>
        client.DELETE(`${P}/users/{user_id}/roles`, {
          params: { path: { user_id: userId }, query: { role, projectId } },
        }),
      ),
    invalidate: () => [qk.users, qk.roles],
  });
}

/**
 * FR-SEC-004: membership is replaced as a set, so one screen edit is one intent — S1 answers with
 * the whole resulting member list, which is what the screen shows.
 */
export function useReplaceMembers(projectId: string) {
  type Vars = { members: Record<string, unknown>[] };
  // UI-REQ-001: the intent has to describe the set being written. Keyed only on the route and the
  // project, a second edit of the same list looked like a replay of the first and S1 answered with
  // the first response.
  const intentOf = (body: Vars) =>
    actionIntent('PUT', '/projects/{project_id}/members', projectId, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<Vars, T.MemberList>({
    mutationKey: ['replaceMembers', projectId],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<T.MemberList>(() =>
        client.PUT(`${P}/projects/{project_id}/members`, {
          params: { path: { project_id: projectId }, header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.members(projectId)],
  });
}

export function useRemoveMember(projectId: string) {
  return useInvalidatingMutation<{ userId: string }, T.ClearedView>({
    mutationKey: ['removeMember', projectId],
    mutationFn: ({ userId }) =>
      unwrap<T.ClearedView>(() =>
        client.DELETE(`${P}/projects/{project_id}/members/{user_id}`, {
          params: { path: { project_id: projectId, user_id: userId } },
        }),
      ),
    invalidate: () => [qk.members(projectId)],
  });
}

/** FR-SEC-003: authorising a person to read PHI_PII / RESTRICTED content, with the reason kept. */
export function useAddGrant(projectId: string) {
  const intentOf = (body: Record<string, unknown>) =>
    actionIntent('POST', '/projects/{project_id}/artifact-grants', projectId, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<Record<string, unknown>, T.GrantView>({
    mutationKey: ['addGrant', projectId],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<T.GrantView>(() =>
        client.POST(`${P}/projects/{project_id}/artifact-grants`, {
          params: { path: { project_id: projectId }, header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.artifactGrants(projectId)],
  });
}

export function useRevokeGrant(projectId: string) {
  return useInvalidatingMutation<{ userId: string; classification: string }, T.ClearedView>({
    mutationKey: ['revokeGrant', projectId],
    mutationFn: ({ userId, classification }) =>
      unwrap<T.ClearedView>(() =>
        client.DELETE(`${P}/projects/{project_id}/artifact-grants/{user_id}`, {
          params: { path: { project_id: projectId, user_id: userId }, query: { classification } },
        }),
      ),
    invalidate: () => [qk.artifactGrants(projectId)],
  });
}

/** CP-WF-020: a production exemption is granted with a reason and an expiry, and can be revoked. */
export function useAddExemption(projectId: string) {
  const intentOf = (body: Record<string, unknown>) =>
    actionIntent('POST', '/projects/{project_id}/production-exemptions', projectId, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<Record<string, unknown>, T.ExemptionView>({
    mutationKey: ['addExemption', projectId],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<T.ExemptionView>(() =>
        client.POST(`${P}/projects/{project_id}/production-exemptions`, {
          params: { path: { project_id: projectId }, header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.productionExemptions(projectId)],
  });
}

export function useRevokeExemption(projectId: string) {
  return useInvalidatingMutation<{ exemptionId: string }, T.ClearedView>({
    mutationKey: ['revokeExemption', projectId],
    mutationFn: ({ exemptionId }) =>
      unwrap<T.ClearedView>(() =>
        client.DELETE(`${P}/production-exemptions/{exemption_id}`, {
          params: { path: { exemption_id: exemptionId } },
        }),
      ),
    invalidate: () => [qk.productionExemptions(projectId)],
  });
}

/**
 * Store or rotate one credential (S5IF-SEC01) — the only request in the whole client that carries
 * a secret value, and the only one whose response is guaranteed not to carry it back.
 *
 * `PUT` rather than `POST`: creating and rotating are the same statement ("this is the value now"),
 * so a retry after a lost response is harmless instead of a second, different write. That is why
 * there is no `Idempotency-Key` here, where every other write in this file sends one: S1 excludes
 * `/secrets` from the replay table because the table stores response bodies verbatim, so the header
 * would be accepted, recorded and then ignored — and the generated types now refuse to send it.
 * The name is part of the intent for the same reason, and the value deliberately is not: an intent
 * becomes a sessionStorage key, so digesting the material would leave a crackable fingerprint of a
 * credential sitting in browser storage.
 */
export function useSaveSecret() {
  return useInvalidatingMutation<{ name: string; material: string }, T.SecretView>({
    mutationKey: ['saveSecret'],
    mutationFn: ({ name, material }) =>
      unwrap<T.SecretView>(() =>
        client.PUT(`${P}/secrets/{name}`, {
          params: { path: { name } },
          body: { material } as never,
        }),
      ),
    // the checklist counts named credentials and reports references that stopped resolving, so a
    // write here changes what step 2 and step 3 are able to say
    invalidate: () => [qk.secrets, qk.setup],
  });
}

/** Remove a credential; 409 `SECRET_IN_USE` while an environment or provider still names it. */
export function useDeleteSecret() {
  return useInvalidatingMutation<{ name: string }, { name: string; deleted: boolean }>({
    mutationKey: ['deleteSecret'],
    // no Idempotency-Key: the contract declares that header on POST/PUT/PATCH only, and the
    // middleware treats DELETE as a mutation whose replay is not cached (X-Operator is injected
    // by the client itself).
    mutationFn: ({ name }) =>
      unwrap<{ name: string; deleted: boolean }>(() =>
        client.DELETE(`${P}/secrets/{name}`, { params: { path: { name } } }),
      ),
    invalidate: () => [qk.secrets, qk.setup],
  });
}

/** UI-PRJ-004: the project settings writes. Membership of the target-environment list is replaced
 *  as a set, so the intent has to describe the set (UI-REQ-001) — two different orders of the same
 *  environments are two different actions, and re-sending the same one is a replay. */
export function useReplaceTargetEnvironments(projectId: string) {
  type Vars = { targetEnvironments: Record<string, unknown>[] };
  const intentOf = (body: Vars) =>
    actionIntent('PUT', '/projects/{project_id}/target-environments', projectId, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<Vars, T.TargetEnvironmentReplaceView>({
    mutationKey: ['replaceTargetEnvironments', projectId],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<T.TargetEnvironmentReplaceView>(() =>
        client.PUT(`${P}/projects/{project_id}/target-environments`, {
          params: { path: { project_id: projectId }, header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.targetEnvironments(projectId), qk.project(projectId), qk.projectList({ includeArchived: false })],
  });
}

export function useSaveProjectSettings(projectId: string) {
  const intentOf = (body: T.ProjectSettingsBody) =>
    actionIntent('PUT', '/projects/{project_id}/settings', projectId, payloadDigest(JSON.stringify(body)));
  return useInvalidatingMutation<T.ProjectSettingsBody, unknown>({
    mutationKey: ['saveProjectSettings', projectId],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<unknown>(() =>
        client.PUT(`${P}/projects/{project_id}/settings`, {
          params: { path: { project_id: projectId }, header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.projectSettings(projectId)],
  });
}

/** Delivery mode A → B. The upgrade is irreversible from the interface, so the caller confirms it
 *  in a dialog before this runs (UI-PRJ-004). */
export function useUpgradeDeliveryMode(projectId: string) {
  const intent = actionIntent('POST', '/projects/{project_id}/upgrade-mode', projectId);
  return useInvalidatingMutation<Record<string, never>, T.UpgradeModeView>({
    mutationKey: ['upgradeDeliveryMode', projectId],
    intent,
    mutationFn: () =>
      unwrap<T.UpgradeModeView>(() =>
        client.POST(`${P}/projects/{project_id}/upgrade-mode`, {
          params: { path: { project_id: projectId }, header: mutatingHeader(intent) },
        }),
      ),
    invalidate: () => [qk.project(projectId), qk.projects, qk.uidPool(projectId)],
  });
}

/** CP-PRJ-009: an import names the export artifact it restores. */
export function useImportProject() {
  const intentOf = (body: { artifactId: string }) =>
    actionIntent('POST', '/projects/import', body.artifactId, payloadDigest(body.artifactId));
  return useInvalidatingMutation<{ artifactId: string }, T.Acceptance>({
    mutationKey: ['importProject'],
    intent: intentOf,
    mutationFn: (body) =>
      unwrap<T.Acceptance>(() =>
        client.POST(`${P}/projects/import`, {
          params: { header: mutatingHeader(intentOf(body)) },
          body: body as never,
        }),
      ),
    invalidate: () => [qk.projects],
  });
}
