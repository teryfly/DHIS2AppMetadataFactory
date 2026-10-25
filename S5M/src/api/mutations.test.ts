/*
 * The writes behind the mobile controls, at the request layer: verb, path, and the idempotency
 * intent that decides whether a retry is the same action. Screens are covered where they carry a
 * decision of their own (features/*); here the point is that a pause can never be replayed as a
 * resume, and that a run cancel addresses the run in the path rather than in a body a proxy would
 * have to read.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount, flushPromises } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { writeJSON, LS } from '@/lib/storage';
import { useCreateChangeRequest, useWorkflowFlag, useCancelRun, useCancelAgentRun, useRegisterModel, useUpdateModel, useTestProvider } from './mutations';

type Sent = { method: string; path: string; key: string | null };
let sent: Sent[];

beforeEach(() => {
  writeJSON(LS.operator, 'Ana');
  sent = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers as HeadersInit | undefined);
      sent.push({ method: (init?.method ?? 'GET').toUpperCase(), path: String(input), key: headers.get('Idempotency-Key') });
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

function mounted<T>(useHook: () => T) {
  const host = defineComponent({
    setup() {
      const hook = useHook();
      return { hook };
    },
    render: () => h('div'),
  });
  const wrapper = mount(host, {
    global: {
      plugins: [[VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { mutations: { retry: 0 } } }) }]],
    },
  });
  return (wrapper.vm as unknown as { hook: T }).hook;
}

describe('workflow control (UM-WB-006)', () => {
  it('posts the verb the operator chose, to the workflow named in the path', async () => {
    const flag = mounted(() => useWorkflowFlag());
    flag.mutate({ workflowId: 'WF-1', projectId: 'PRJ-1', flag: 'pause' });
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ method: 'POST', path: '/api/v1/workflows/WF-1/pause' });

    flag.mutate({ workflowId: 'WF-1', projectId: 'PRJ-1', flag: 'resume' });
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ method: 'POST', path: '/api/v1/workflows/WF-1/resume' });
  });

  it('separates the verbs by intent, and mints a fresh key once an action has succeeded', async () => {
    const flag = mounted(() => useWorkflowFlag());
    flag.mutate({ workflowId: 'WF-1', projectId: 'PRJ-1', flag: 'cancel' });
    await flushPromises();
    const firstCancel = sent.at(-1)?.key;
    flag.mutate({ workflowId: 'WF-1', projectId: 'PRJ-1', flag: 'cancel' });
    await flushPromises();
    const secondCancel = sent.at(-1)?.key;
    flag.mutate({ workflowId: 'WF-1', projectId: 'PRJ-1', flag: 'pause' });
    await flushPromises();
    const pause = sent.at(-1)?.key;

    const uuid = /[0-9a-f-]{36}/i;
    expect(firstCancel).toMatch(uuid);
    expect(pause).toMatch(uuid);
    // the verb is inside the intent, which is what keeps a retry of a cancel from ever arriving as
    // a pause -- two different actions sharing one stored response is IDEMPOTENCY_CONFLICT
    expect(firstCancel).not.toBe(pause);
    // the first cancel completed, so its key was released: a second press is a new action, not a
    // replay of an answer the server has already given
    expect(secondCancel).not.toBe(firstCancel);
  });
});

describe('deployment run cancel', () => {
  it('addresses the run in the path', async () => {
    const cancel = mounted(() => useCancelRun());
    cancel.mutate({ runId: 'RUN-1', workflowId: 'WF-1' });
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ method: 'POST', path: '/api/v1/deployment-runs/RUN-1/cancel' });
    expect(sent.at(-1)?.key).toMatch(/[0-9a-f-]{36}/i);
  });
});

describe('agent run cancel', () => {
  it('addresses the run under the workflow that owns it', async () => {
    const cancel = mounted(() => useCancelAgentRun());
    cancel.mutate({ workflowId: 'WF-1', projectId: 'PRJ-1', runId: 'AR-1', stageId: 'ST-1' });
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ method: 'POST', path: '/api/v1/workflows/WF-1/agent-runs/AR-1/cancel' });
    expect(sent.at(-1)?.key).toMatch(/[0-9a-f-]{36}/i);
  });
});

describe('model registry (UM-SET-005, S5IF-GOV05 / GOV06)', () => {
  it('registers a model against the provider it belongs to, once per intent', async () => {
    const register = mounted(() => useRegisterModel());
    register.mutate({ provider: 'openai', model: 'gpt-x-large', enabled: true, unitPriceInputPer1k: 0, unitPriceOutputPer1k: 0 });
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ method: 'POST', path: '/api/v1/governance/models' });
    expect(sent.at(-1)?.key).toMatch(/[0-9a-f-]{36}/i);
  });

  it('addresses the model by its reference in the path, not in the body', async () => {
    const toggle = mounted(() => useUpdateModel());
    toggle.mutate({ ref: 'openai/gpt-x-large', body: { enabled: false } });
    await flushPromises();
    // the reference carries a slash; S1 declares `{ref:path}` and the client encodes it
    expect(sent.at(-1)).toMatchObject({ method: 'PATCH', path: '/api/v1/governance/models/openai%2Fgpt-x-large' });
  });
});

describe('provider reachability probe (S5IF-GOV20)', () => {
  it('posts the probe to the provider named in the path, with no body', async () => {
    const probe = mounted(() => useTestProvider());
    probe.mutate({ name: 'poe' });
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ method: 'POST', path: '/api/v1/governance/providers/poe/test-connection' });
  });

  it('lets the same provider be probed twice', async () => {
    const probe = mounted(() => useTestProvider());
    probe.mutate({ name: 'poe' });
    await flushPromises();
    probe.mutate({ name: 'poe' });
    await flushPromises();
    const keys = sent.map((s) => s.key);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

/*
 * CP-API-003: a completed idempotency key is replayed byte-for-byte, and the same key sent with a
 * corrected request comes back 422 IDEMPOTENCY_CONFLICT. A refusal the server answered therefore
 * spent its key — holding it would pin every later press of the same control to that old refusal,
 * with no way for the operator to act again. UM-NET-001's reuse rule is about the attempt nobody
 * answered, not about the one that was answered and refused.
 */
describe('the idempotency key a refusal leaves behind (CP-API-003, UM-NET-001)', () => {
  function answerWith(status: number, errorCode: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers as HeadersInit | undefined);
        sent.push({
          method: (init?.method ?? 'GET').toUpperCase(),
          path: String(input),
          key: headers.get('Idempotency-Key'),
        });
        return new Response(JSON.stringify({ errorCode, message: 'no', retryable: false }), {
          status,
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
  }

  async function pressTwice(runId: string): Promise<(string | null | undefined)[]> {
    const cancel = mounted(() => useCancelRun());
    cancel.mutate({ runId, workflowId: 'WF-1' });
    await flushPromises();
    const first = sent.at(-1)?.key;
    cancel.mutate({ runId, workflowId: 'WF-1' });
    await flushPromises();
    expect(first).toMatch(/[0-9a-f-]{36}/i);
    return [first, sent.at(-1)?.key];
  }

  it('drops the key after an answered refusal, so the next press is a new request', async () => {
    answerWith(422, 'VALIDATION_FAILED');
    const [first, second] = await pressTwice('RUN-REFUSED');
    expect(second).not.toBe(first);
  });

  it('drops it after a state conflict too', async () => {
    answerWith(409, 'GATE_NOT_APPROVED');
    const [first, second] = await pressTwice('RUN-CONFLICTED');
    expect(second).not.toBe(first);
  });

  it('keeps the key while the server is still working on this very request', async () => {
    answerWith(409, 'REQUEST_IN_PROGRESS');
    const [first, second] = await pressTwice('RUN-IN-FLIGHT');
    expect(second).toBe(first);
  });

  it('keeps the key when nothing answered', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers as HeadersInit | undefined);
        sent.push({
          method: (init?.method ?? 'GET').toUpperCase(),
          path: String(input),
          key: headers.get('Idempotency-Key'),
        });
        return Promise.reject(new TypeError('offline'));
      }),
    );
    const [first, second] = await pressTwice('RUN-UNANSWERED');
    expect(second).toBe(first);
  });
});

/*
 * CP-CR-005 with 总体 SRS 15.6: a directed deletion is authorized by the change request itself —
 * S2's impact read has no deletion field to fill it in. The mobile form therefore has to send the
 * names, or the object is never retired on the instance.
 */
describe('change request creation (UM-OPS-001)', () => {
  it('sends the retirements the request authorizes', async () => {
    const bodies: unknown[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const headers = new Headers(init?.headers as HeadersInit | undefined);
        sent.push({
          method: (init?.method ?? 'GET').toUpperCase(),
          path: String(input),
          key: headers.get('Idempotency-Key'),
        });
        bodies.push(init?.body ? JSON.parse(String(init.body)) : null);
        return new Response('{}', { status: 201, headers: { 'content-type': 'application/json' } });
      }),
    );
    const create = mounted(() => useCreateChangeRequest());
    create.mutate({
      projectId: 'PRJ-1',
      body: { reason: 'retire', affectedBusinessKeys: ['dataElement:old'], deletionCandidates: ['dataElement:old'] },
    });
    await flushPromises();
    expect(sent.at(-1)?.path).toBe('/api/v1/projects/PRJ-1/change-requests');
    expect(bodies.at(-1)).toMatchObject({ reason: 'retire', deletionCandidates: ['dataElement:old'] });
  });
});
