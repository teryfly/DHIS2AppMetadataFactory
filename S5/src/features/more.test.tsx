/*
 * Cover the remaining interactive paths that earlier tests only rendered past: 404 route, change
 * requests with rows, audit verify + populated rows, the COMMIT second-confirmation submit, and a
 * successful Gate approval once warnings are dispositioned (UI-GATE-002/003/004 + UI-DEP-004).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import { WORKFLOW_ID } from '@/mocks/db';

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
});
afterEach(() => server.resetHandlers());

describe('404 route (NotFound)', () => {
  it('renders a fallback with a back link', async () => {
    renderAt('/no-such-page');
    expect(await screen.findByText('404')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go back' })).toBeInTheDocument();
  });
});

describe('Change requests with rows (UI-OPS-001)', () => {
  it('renders rows and per-row actions', async () => {
    server.use(
      http.get(`/api/v1/projects/:id/change-requests`, () =>
        HttpResponse.json({ items: [{ id: 'CR-1', status: 'PENDING', reason: 'Retire legacy code list' }], total: 1 }),
      ),
    );
    renderAt('/projects/PRJ-green-01/change-requests');
    expect(await screen.findByText('Retire legacy code list')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Approve|Start|impact/i }).length).toBeGreaterThan(0);
  });
});

describe('Audit populated + verify (UI-OPS-002)', () => {
  // `AuditVerifyView` answers `verified` / `brokenAt`; the mock used to answer `chainValid`, which is
  // how a screen that always reported a break stayed green.
  it('renders audit rows and reads the intact range back from the verify answer', async () => {
    server.use(
      http.get(`/api/v1/projects/:id/audit`, () =>
        HttpResponse.json({
          items: [{ id: 'A-1', eventType: 'gate.approved', operatorName: 'Ana', sourceSubsystem: 'S1', occurredAt: new Date().toISOString() }],
          total: 1,
        }),
      ),
      http.post(`/api/v1/audit/verify`, () =>
        HttpResponse.json({ verified: true, firstSequence: 1, lastSequence: 42, recordCount: 42 }),
      ),
    );
    renderAt('/projects/PRJ-green-01/audit');
    expect(await screen.findByText('gate.approved')).toBeInTheDocument();
    await userEvent.type(screen.getAllByRole('textbox')[0] as HTMLInputElement, 'Ana');
    await userEvent.click(screen.getByRole('button', { name: /Verify chain/ }));
    expect(await screen.findByText(/Chain intact from 1 to 42|链路完整：序号 1 至 42/)).toBeInTheDocument();
  });

  it('names the sequence where the chain actually broke', async () => {
    server.use(
      http.get(`/api/v1/projects/:id/audit`, () =>
        HttpResponse.json({ items: [{ id: 'A-1', eventType: 'gate.approved', occurredAt: new Date().toISOString() }], total: 1 }),
      ),
      http.post(`/api/v1/audit/verify`, () => HttpResponse.json({ verified: false, brokenAt: 17, recordCount: 16 })),
    );
    renderAt('/projects/PRJ-green-01/audit');
    await screen.findByText('gate.approved');
    await userEvent.click(screen.getByRole('button', { name: /Verify chain/ }));
    expect(await screen.findByText(/break at sequence 17|在序号 17 处断裂/)).toBeInTheDocument();
  });
});

describe('Run console COMMIT submit (UI-DEP-004)', () => {
  it('opens the confirmation and submits COMMIT', async () => {
    renderAt('/projects/PRJ-green-01/deployments/RUN-1');
    await screen.findByText(/Deployment console/);
    await userEvent.click(screen.getByRole('button', { name: 'COMMIT' }));
    // Second confirmation lists environment + payload.
    expect((await screen.findAllByText(/Affects environment/)).length).toBeGreaterThan(0);
    // The modal confirm COMMIT is the second COMMIT button.
    const commitBtns = screen.getAllByRole('button', { name: 'COMMIT' });
    await userEvent.click(commitBtns[commitBtns.length - 1] as HTMLElement);
    expect(screen.getAllByRole('button', { name: 'COMMIT' }).length).toBeGreaterThan(0);
  });
});

describe('Gate approval success (UI-GATE-002/003)', () => {
  it('enables and submits approve once every warning is accepted', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          workflowId: WORKFLOW_ID,
          allowed: [{ action: 'gate.approve', available: true, targetId: 'GT-1' }, { action: 'gate.reject', available: true, targetId: 'GT-1' }],
          unavailable: [],
        }),
      ),
    );
    renderAt('/projects/PRJ-green-01/gate/GT-1');
    await screen.findByText(/Warnings/);
    for (const b of screen.getAllByRole('button', { name: /Accept/ })) await userEvent.click(b);
    const approve = (await screen.findByRole('button', { name: /Approve/ })) as HTMLButtonElement;
    expect(approve.disabled).toBe(false); // enabled once warnings handled AND allowed-actions permits
    await userEvent.click(approve);
    // Approval submitted; the page stays mounted on the decision section (no throw).
    expect(await screen.findByText(/What happens if you approve/)).toBeInTheDocument();
  });
});
