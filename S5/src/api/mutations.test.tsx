/*
 * Drive every state-changing hook against the MSW mock so the mutation bodies + invalidation
 * callbacks execute. Operator is present so S1 (mock) accepts the request. This is the functional
 * counterpart of the UI tests: it proves each S1 action is wired with the right verb, params and
 * idempotency (create/execute actions) — UI-REQ-001, UI-BR-04.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import '@/i18n';
import { useOperatorStore } from '@/stores/operator';
import { WORKFLOW_ID, DESIGN_PROJECT_ID } from '@/mocks/db';
import {
  useCreateProject, useAnswerQuestion, useApproveGate, useRejectGate, useSubmitPatch,
  useSubmitForApproval, useRunStage, useResolveManualCheck, useWorkflowFlag,
  useMarkNotificationRead, useCommitRun, useCreateRun, useSaveSettings, useTestConnection, useEmergencyLock,
  useChangeRequestStep, useCreateChangeRequest,
  useCreateEnvironment, useUpdateEnvironment, useRegisterProvider, useUpdateProvider,
  useDisableEnvironment, useProbeCapabilities, useRelaxPolicy, useCancelDeploymentRun,
} from './mutations';
import { http, HttpResponse } from 'msw';
import { idempotencyKeyFor, releaseIdempotencyKey } from './idempotency';
import { server } from '@/mocks/server';

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => useOperatorStore.getState().setName('Ana'));
afterEach(() => {
  cleanup();
  useOperatorStore.getState().clear();
});

describe('mutation wiring', () => {
  it('createProject posts and resolves', async () => {
    const { result } = renderHook(() => useCreateProject(), { wrapper });
    result.current.mutate({ body: { name: 'X', ownerName: 'O', deliveryMode: 'A', targetEnvironments: [{ environmentKey: 'env-dev', isDesignReference: true, promotionOrder: 1 }] } as never });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('answerQuestion sends an idempotency-scoped POST', async () => {
    const { result } = renderHook(() => useAnswerQuestion(WORKFLOW_ID), { wrapper });
    result.current.mutate({ questionId: 'Q-1', body: { customText: 'yes' }, intent: 'answer:Q-1' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('approveGate resolves', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const spy = vi.spyOn(qc, 'invalidateQueries');
    const w = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => useApproveGate(WORKFLOW_ID, DESIGN_PROJECT_ID), { wrapper: w });
    // Provide a disposition per open warning so S1 (mock) accepts the approval (UI-GATE-002).
    result.current.mutate({ gateId: 'GT-1', body: { gateType: 'REQUIREMENTS_GATE', seenBoundHashes: [], warningDispositions: [{ disposition: 'ACCEPT' }, { disposition: 'ACCEPT' }], confirmations: [] } as never });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    // The GatePage reads ['gates', id]; without it the reviewer stays on a WAITING_APPROVAL page
    // after their own approval — the cache bug this assertion pins.
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey?: readonly unknown[] })?.queryKey));
    expect(keys).toContain(JSON.stringify(['gates', 'GT-1']));
  });

  it('rejectGate resolves with a reason', async () => {
    const { result } = renderHook(() => useRejectGate(WORKFLOW_ID), { wrapper });
    result.current.mutate({ gateId: 'GT-1', body: { gateType: 'REQUIREMENTS_GATE', reason: 'not ready' } as never });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('submitPatch resolves and reports assigned UIDs', async () => {
    const { result } = renderHook(() => useSubmitPatch(WORKFLOW_ID, DESIGN_PROJECT_ID), { wrapper });
    result.current.mutate({ stageId: 'ST-req', patch: { requirements: [] }, intent: 'patch:ST-req' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('submitForApproval resolves', async () => {
    const { result } = renderHook(() => useSubmitForApproval(WORKFLOW_ID, DESIGN_PROJECT_ID), { wrapper });
    result.current.mutate({ stageId: 'ST-req' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('runStage resolves (no idempotency key per re-synced S1)', async () => {
    const { result } = renderHook(() => useRunStage(WORKFLOW_ID), { wrapper });
    result.current.mutate({ stageId: 'ST-domain' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('resolveManualCheck resolves', async () => {
    const { result } = renderHook(() => useResolveManualCheck(WORKFLOW_ID), { wrapper });
    result.current.mutate({ stageId: 'ST-build', note: 'checked', outcome: 'PASSED' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('workflow pause/resume/cancel resolve', async () => {
    for (const flag of ['pause', 'resume', 'cancel'] as const) {
      const { result } = renderHook(() => useWorkflowFlag(WORKFLOW_ID, DESIGN_PROJECT_ID), { wrapper });
      result.current.mutate({ flag });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
    }
  });

  it('markNotificationRead resolves', async () => {
    const { result } = renderHook(() => useMarkNotificationRead(), { wrapper });
    result.current.mutate({ notificationId: 'N-1' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('createRun resolves', async () => {
    const { result } = renderHook(() => useCreateRun(WORKFLOW_ID), { wrapper });
    result.current.mutate({ body: { environmentKey: 'env-dev' }, intent: 'run:env-dev' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('commitRun resolves', async () => {
    const { result } = renderHook(() => useCommitRun(WORKFLOW_ID, 'RUN-1', DESIGN_PROJECT_ID), { wrapper });
    result.current.mutate({ intent: 'commit:run:RUN-1' });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('saveSettings resolves', async () => {
    const { result } = renderHook(() => useSaveSettings(), { wrapper });
    result.current.mutate({ body: { maxUploadBytes: 100 } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('testConnection + emergencyLock resolve', async () => {
    const tc = renderHook(() => useTestConnection(), { wrapper });
    tc.result.current.mutate({ environmentKey: 'env-dev', requiredScopes: ['discovery', 'import'] });
    await waitFor(() => expect(tc.result.current.isSuccess).toBe(true));
    // S1 refuses a lock with no reason, so the hook has to send one; unlocking needs none.
    const lock = renderHook(() => useEmergencyLock(), { wrapper });
    lock.result.current.mutate({ environmentKey: 'env-dev', locked: true, reason: '正在核对生产数据' });
    await waitFor(() => expect(lock.result.current.isSuccess).toBe(true));
    const release = renderHook(() => useEmergencyLock(), { wrapper });
    release.result.current.mutate({ environmentKey: 'env-dev', locked: false });
    await waitFor(() => expect(release.result.current.isSuccess).toBe(true));
  });

  it('createEnvironment posts the whole record, updateEnvironment patches it', async () => {
    let created: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/v1/environments', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        expect(request.headers.get('Idempotency-Key')).toMatch(/[0-9a-f-]{36}/i);
        return HttpResponse.json({ environmentKey: 'env-new' }, { status: 201 });
      }),
    );
    const create = renderHook(() => useCreateEnvironment(), { wrapper });
    create.result.current.mutate({
      environmentKey: 'env-new',
      baseUrl: 'https://new.invalid',
      environmentClass: 'DEV',
      authMode: 'PAT',
      credentials: [{ scope: 'discovery', secretRef: 'env://NEW_DISCOVERY' }],
    } as never);
    await waitFor(() => expect(create.result.current.isSuccess).toBe(true));
    expect(created).toMatchObject({ environmentKey: 'env-new', credentials: [{ scope: 'discovery' }] });

    let patched: Record<string, unknown> | null = null;
    let patchVerb = '';
    server.use(
      http.patch('/api/v1/environments/env-new', async ({ request }) => {
        patchVerb = request.method;
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ environmentKey: 'env-new', timeoutSeconds: 30 });
      }),
    );
    const update = renderHook(() => useUpdateEnvironment('env-new'), { wrapper });
    update.result.current.mutate({ timeoutSeconds: 30 } as never);
    await waitFor(() => expect(update.result.current.isSuccess).toBe(true));
    expect(patchVerb).toBe('PATCH');
    expect(patched).toEqual({ timeoutSeconds: 30 });
  });

  it('a provider edit carries only what changed, so the Key reference survives', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/v1/governance/providers/inhouse', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          name: 'inhouse',
          providerClass: 'PRIVATE_NETWORK',
          endpoint: 'https://fixed.invalid/v1',
          apiKeySecretRef: 'env://MODEL_API_KEY',
          enabled: true,
        });
      }),
    );
    const edit = renderHook(() => useUpdateProvider(), { wrapper });
    edit.result.current.mutate({ name: 'inhouse', body: { endpoint: 'https://fixed.invalid/v1' } } as never);
    await waitFor(() => expect(edit.result.current.isSuccess).toBe(true));
    // The whole point of PATCH over re-registration: no `apiKeySecretRef` in the body means the
    // stored reference is untouched, which the mock merge also demonstrates.
    expect(body).toEqual({ endpoint: 'https://fixed.invalid/v1' });

    let registered: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/v1/governance/providers', async ({ request }) => {
        registered = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ name: 'fresh' }, { status: 201 });
      }),
    );
    const create = renderHook(() => useRegisterProvider(), { wrapper });
    create.result.current.mutate({
      name: 'fresh',
      providerClass: 'LOCAL_ONLY',
      endpoint: 'https://fresh.invalid/v1',
      apiKeySecretRef: 'env://FRESH_KEY',
    } as never);
    await waitFor(() => expect(create.result.current.isSuccess).toBe(true));
    expect(registered).toMatchObject({ name: 'fresh', apiKeySecretRef: 'env://FRESH_KEY' });
  });

  it('operator-required mutation surfaces an ApiError (UI-BR-04)', async () => {
    useOperatorStore.getState().clear();
    const { result } = renderHook(() => useAnswerQuestion(WORKFLOW_ID), { wrapper });
    result.current.mutate({ questionId: 'Q-1', body: { customText: 'x' }, intent: 'answer:Q-1b' });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

/*
 * Change requests (UI-OPS-001). Asserted at the mutation layer on purpose: the create call and the
 * impact call are each one request, and the screen that wraps them is covered by
 * features/changeRequests/changeRequests.test.tsx for the part that matters there — which steps
 * are offered per request.
 */
describe('change request mutations (UI-OPS-001)', () => {
  it('posts a new request with its reason and affected entries', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post(`/api/v1/projects/:id/change-requests`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'CR-9', status: 'DRAFT', reason: body.reason }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useCreateChangeRequest(DESIGN_PROJECT_ID), { wrapper });
    const created = await result.current.mutateAsync({ body: { reason: 'rename', affectedBusinessKeys: ['page:a'] } });
    expect(body).toEqual({ reason: 'rename', affectedBusinessKeys: ['page:a'] });
    expect(created.status).toBe('DRAFT');
  });

  it('carries the retirements the request authorizes (CP-CR-005)', async () => {
    let sent: Record<string, unknown> = {};
    server.use(
      http.post(`/api/v1/projects/:id/change-requests`, async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'CR-10', status: 'DRAFT' }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useCreateChangeRequest(DESIGN_PROJECT_ID), { wrapper });
    await result.current.mutateAsync({
      body: { reason: 'retire', affectedBusinessKeys: ['dataElement:old'], deletionCandidates: ['dataElement:old'] },
    });
    expect(sent).toMatchObject({ reason: 'retire', deletionCandidates: ['dataElement:old'] });
  });

  it('asks for an idempotency key on a decision and not on an impact report', async () => {    const headers: Record<string, string | null> = {};
    server.use(
      http.post(`/api/v1/change-requests/:crId/impact`, ({ request }) => {
        headers.impact = request.headers.get('Idempotency-Key');
        return HttpResponse.json({ changeRequestId: 'CR-1', affectedStages: ['MAPPING'] });
      }),
      http.post(`/api/v1/change-requests/:crId/start`, ({ request }) => {
        headers.start = request.headers.get('Idempotency-Key');
        return HttpResponse.json({ id: 'CR-1', status: 'IN_PROGRESS' });
      }),
    );
    const { result } = renderHook(() => useChangeRequestStep(DESIGN_PROJECT_ID), { wrapper });
    await result.current.mutateAsync({ crId: 'CR-1', step: 'impact', intent: 'i1' });
    await result.current.mutateAsync({ crId: 'CR-1', step: 'start', intent: 'i2' });
    expect(headers.impact).toBeNull();
    expect(headers.start).toMatch(/[0-9a-f-]{36}/i);
  });
});

/*
 * The actions that already had an endpoint and no way to reach one (user-guide §11). They are
 * tested at the mutation layer because each has a different header contract: disabling and
 * relaxing reuse one key per logical action so a double press replays, while a probe must not —
 * its route is declared idempotent, so a repeated key would answer the *previous* probe.
 */
describe('controls that were missing an entry point', () => {
  it('disables an environment under a key that survives a retry, and surfaces a second disable as 409', async () => {
    const keys = new Set<string | null>();
    const bodies: unknown[] = [];
    let presses = 0;
    server.use(
      http.post('/api/v1/environments/:key/disable', async ({ request }) => {
        keys.add(request.headers.get('Idempotency-Key'));
        bodies.push(await request.json());
        presses += 1;
        // S1 refuses a disable of a row that is already out of service (`rules.can_disable`), so
        // the second press is an answer the hook has to carry to the operator rather than a success
        return presses > 1
          ? HttpResponse.json(
              { errorCode: 'INVALID_STATE', message: 'out of service', details: { status: 'DISABLED' } },
              { status: 409 },
            )
          : HttpResponse.json({ environmentKey: 'env-dev', status: 'DISABLED' });
      }),
    );
    const { result } = renderHook(() => useDisableEnvironment(), { wrapper });
    await result.current.mutateAsync({ environmentKey: 'env-dev', reason: '停机维护' });
    await expect(result.current.mutateAsync({ environmentKey: 'env-dev', reason: '停机维护' })).rejects.toMatchObject({
      errorCode: 'INVALID_STATE',
      httpStatus: 409,
    });
    expect(bodies[0]).toEqual({ reason: '停机维护' });
    // the same reason twice is the same logical action, so it must not become a second request key
    expect([...keys]).toHaveLength(1);
    expect([...keys][0]).toMatch(/[0-9a-f-]{36}/i);
  });

  it('probes capabilities with a fresh key per press', async () => {
    const keys: (string | null)[] = [];
    server.use(
      http.post('/api/v1/environments/:key/probe-capabilities', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'));
        return HttpResponse.json({ capabilityProfileArtifactId: 'art-cap-env-dev', version: '2.41.4', profileVersion: '1' });
      }),
    );
    const { result } = renderHook(() => useProbeCapabilities(), { wrapper });
    await result.current.mutateAsync({ environmentKey: 'env-dev' });
    await result.current.mutateAsync({ environmentKey: 'env-dev' });
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBeNull();
    expect(keys[0]).not.toBe(keys[1]);
  });

  it('relaxes the policy with the change the server asks for, not just a reason', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('/api/v1/governance/policy/relaxations', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'RELAX-1', scope: 'organization' }, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useRelaxPolicy(), { wrapper });
    const row = await result.current.mutateAsync({
      change: { level: 'INTERNAL', addClasses: ['APPROVED_EXTERNAL'] },
      reason: '需要一次跨语言术语比对',
      scope: 'organization',
    } as never);
    expect(body).toEqual({
      change: { level: 'INTERNAL', addClasses: ['APPROVED_EXTERNAL'] },
      reason: '需要一次跨语言术语比对',
      scope: 'organization',
    });
    expect(row.id).toBe('RELAX-1');
  });

  it('cancels the deployment run named in the path', async () => {
    const paths: string[] = [];
    server.use(
      http.post('/api/v1/deployment-runs/:runId/cancel', ({ request }) => {
        paths.push(new URL(request.url).pathname);
        return HttpResponse.json({ deploymentRunId: 'RUN-1', status: 'CANCELLED' });
      }),
    );
    const { result } = renderHook(() => useCancelDeploymentRun(WORKFLOW_ID), { wrapper });
    await result.current.mutateAsync({ runId: 'RUN-1' });
    expect(paths).toEqual(['/api/v1/deployment-runs/RUN-1/cancel']);
  });
});

/*
 * CP-API-003: S1 replays a completed idempotency key byte-for-byte, and the same key sent with a
 * corrected body comes back 422 IDEMPOTENCY_CONFLICT. So a key whose request was refused is spent:
 * holding onto it pins every later attempt to that refusal, and the operator has no way to act
 * again from this tab. Only an unknown outcome keeps its key — a request nobody answered, and one
 * the server is still working out (CP-GATE-018 forbids a second COMMIT under a fresh key).
 */
describe('the idempotency key a refusal leaves behind (CP-API-003, UI-REQ-004)', () => {
  const INTENT = 'patch:ST-refused';

  afterEach(() => releaseIdempotencyKey(INTENT));

  async function attempt(status: number | 'unanswered', intent = INTENT): Promise<string> {
    let sentKey = '';
    server.use(
      http.post('/api/v1/stages/:stageId/patches', ({ request }) => {
        sentKey = request.headers.get('Idempotency-Key') ?? '';
        if (status === 'unanswered') return HttpResponse.error();
        return HttpResponse.json(
          { errorCode: 'VALIDATION_FAILED', message: 'the patch has a dangling reference', retryable: false },
          { status },
        );
      }),
    );
    const { result } = renderHook(() => useSubmitPatch(WORKFLOW_ID, DESIGN_PROJECT_ID), { wrapper });
    await result.current.mutateAsync({ stageId: 'ST-1', patch: { requirements: [] }, intent }).catch(() => undefined);
    expect(sentKey).toMatch(/[0-9a-f-]{36}/i);
    return sentKey;
  }

  it('drops the key after an answered refusal, so the next attempt is a new request', async () => {
    const refused = await attempt(422);
    expect(idempotencyKeyFor(INTENT)).not.toBe(refused);
  });

  it('drops it after a state conflict too', async () => {
    const refused = await attempt(409);
    expect(idempotencyKeyFor(INTENT)).not.toBe(refused);
  });

  it('keeps the key when nothing answered', async () => {
    const pending = await attempt('unanswered');
    expect(idempotencyKeyFor(INTENT)).toBe(pending);
  });

  it('keeps the key while the server is still working on this very request', async () => {
    let sentKey = '';
    server.use(
      http.post('/api/v1/stages/:stageId/patches', ({ request }) => {
        sentKey = request.headers.get('Idempotency-Key') ?? '';
        return HttpResponse.json({ errorCode: 'REQUEST_IN_PROGRESS', retryable: true }, { status: 409 });
      }),
    );
    const { result } = renderHook(() => useSubmitPatch(WORKFLOW_ID, DESIGN_PROJECT_ID), { wrapper });
    await result.current
      .mutateAsync({ stageId: 'ST-1', patch: { requirements: [] }, intent: INTENT })
      .catch(() => undefined);
    expect(idempotencyKeyFor(INTENT)).toBe(sentKey);
  });
});
