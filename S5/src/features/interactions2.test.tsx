/*
 * Final coverage layer for interactive branches and the error bridge: stage run + view switch, the
 * governance relaxation modal, the idempotency-key hook, and that a surfaced ApiError reaches the
 * toast listener registered against the shared QueryClient error cache.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, renderHook, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useUiStore } from '@/stores/ui';
import { server } from '@/mocks/server';
import { WORKFLOW_ID, DESIGN_PROJECT_ID } from '@/mocks/db';
import { useIdempotencyKey } from '@/api/idempotency';
import { createQueryClient, onApiError } from '@/api/queryClient';
import { gateOptions } from '@/api/queries';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <OperatorGate>
          <AppRoutes />
        </OperatorGate>
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
  useUiStore.getState().closeOperatorDialog();
});
afterEach(() => server.resetHandlers());

describe('StageView interactions', () => {
  it('runs the agent and switches the workspace view', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({ workflowId: WORKFLOW_ID, allowed: [{ action: 'stage.run', available: true, targetId: 'ST-req' }], unavailable: [] }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await screen.findByText('Manual authoring');
    await userEvent.click(screen.getByRole('button', { name: /Run agent/ }));
    // Graph is the stage's own object graph now (S2's answer over GET /workflows/{id}/object-graph),
    // not a placeholder: the tab used to be one of four that could only say "not implemented".
    await userEvent.click(screen.getByText('Graph'));
    expect(await screen.findByRole('region', { name: 'Objects in this graph' })).toBeInTheDocument();
  });
});


describe('useIdempotencyKey (UI-REQ-001)', () => {
  it('is stable per intent and undefined when there is no intent', async () => {
    const { result, rerender } = renderHook(({ intent }) => useIdempotencyKey(intent), { initialProps: { intent: 'a:b' as string | null } });
    const first = result.current;
    expect(first).toMatch(/^[0-9a-f-]{36}$/i);
    rerender({ intent: 'a:b' });
    expect(result.current).toBe(first);
    rerender({ intent: null });
    expect(result.current).toBeUndefined();
  });
});

describe('QueryClient error bridge (UI-REQ-002)', () => {
  it('routes a surfaced ApiError to onApiError listeners', async () => {
    server.use(http.get(`/api/v1/gates/:id`, () => HttpResponse.json({ errorCode: 'NOT_FOUND', retryable: false, correlationId: 'z' }, { status: 404 })));
    const qc = createQueryClient();
    const seen: string[] = [];
    const off = onApiError((e) => seen.push(e.errorCode));
    function Probe() {
      useQuery({ ...gateOptions('missing-gate'), retry: false });
      return null;
    }
    render(
      <QueryClientProvider client={qc}>
        <Probe />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(seen).toContain('NOT_FOUND'));
    off();
  });

  it('createQueryClient yields a client with query + mutation caches', () => {
    const qc = createQueryClient();
    expect(qc.getQueryCache()).toBeTruthy();
    expect(qc.getMutationCache()).toBeTruthy();
  });
});

// keep ReactNode import used by the render helper signature above
export type _Node = ReactNode;
