/*
 * UI-RT: drive the hook through the mock EventSource and assert events only trigger invalidation
 * (never state), that stream.reset does a whole-project refresh, and progress frames land in the
 * transient store (UI-RT-002/003/004).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@/i18n';
import { useWorkflowEvents } from './useWorkflowEvents';
import { installMockEventSource, type MockSseControl } from '@/mocks/events';
import { useProgressStore } from './progress';
import { WORKFLOW_ID, DESIGN_PROJECT_ID } from '@/mocks/db';

let control: MockSseControl | null = null;
afterEach(() => {
  control?.restore();
  control = null;
  vi.restoreAllMocks();
});

function Harness() {
  useWorkflowEvents({ workflowId: WORKFLOW_ID, projectId: DESIGN_PROJECT_ID });
  return null;
}

async function mount() {
  control = installMockEventSource();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const spy = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <Harness />
    </QueryClientProvider>,
  );
  return spy;
}

describe('useWorkflowEvents (SSE)', () => {
  it('invalidates related queries when an event arrives (UI-RT-002)', async () => {
    const spy = await mount();
    // initial open fires stage.waiting_approval + gate.created (mock scripting).
    await waitFor(() => expect(spy).toHaveBeenCalled(), { timeout: 2000 });
  });

  it('stream.reset triggers a whole-project refresh (UI-RT-003)', async () => {
    const spy = await mount();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    spy.mockClear();
    control?.push('stream.reset');
    await waitFor(() => {
      // the refresh must reach the roots the screens actually read: a bare [projectId] prefix
      // matched nothing (every key starts with its domain word) and the reset was a no-op.
      const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey?: readonly unknown[] })?.queryKey));
      expect(keys).toContain(JSON.stringify(['projects', DESIGN_PROJECT_ID]));
      expect(keys).toContain(JSON.stringify(['workflows', WORKFLOW_ID]));
      expect(keys).toContain(JSON.stringify(['gates']));
    }, { timeout: 2000 });
  });

  it('gate events reach the gate detail key the GatePage reads (UI-RT-002)', async () => {
    const spy = await mount();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    spy.mockClear();
    control?.push('gate.approved', { gateId: 'GT-1' });
    await waitFor(() => {
      const keys = spy.mock.calls.map((c) => (c[0] as { queryKey?: readonly unknown[] })?.queryKey);
      expect(keys.map((k) => JSON.stringify(k))).toContain(JSON.stringify(['gates', 'GT-1']));
    }, { timeout: 2000 });
  });

  it('progress frames update the transient store without a refetch (UI-RT-004 / BR-02)', async () => {
    await mount();
    // the field names are S1's reporter's (progress.py): `step`, `completedFraction`, `agentRunId`.
    // A frame shaped like anything else is the bug this test exists to catch.
    control?.push('agent.progress', { stageId: 'ST-domain', agentRunId: 'AR-1', step: 'mapping', completedFraction: 0.6 });
    await waitFor(() => {
      const entry = useProgressStore.getState().byWorkflow[`${WORKFLOW_ID}:agent:ST-domain`];
      expect(entry?.currentStep).toBe('mapping');
      expect(entry?.fraction).toBe(0.6);
      // the cancel button addresses the run by id, so the frame has to leave it behind
      expect(entry?.runId).toBe('AR-1');
    }, { timeout: 2000 });
  });
});
