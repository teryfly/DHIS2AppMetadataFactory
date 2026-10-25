/*
 * UI-OPS-002: the audit list is filtered by the server (object / event type / operator / time) and
 * the COMMIT trace is read by execution id. The filters matter as query parameters rather than as
 * page trimming: a page of the newest records simply does not contain older ones.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';

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

/**
 * The field primitives render a real `for`-linked label, so the filter is reached by the name a
 * screen reader hears (UI-NFR-03) rather than by poking at a library's markup.
 */
function inputFor(labelText: RegExp): HTMLInputElement {
  return screen.getByLabelText(labelText) as HTMLInputElement;
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('Audit filters (UI-OPS-002)', () => {
  it('sends the filter as a query parameter and shows only what S1 returns', async () => {
    const queried: string[] = [];
    server.use(
      http.get(`/api/v1/projects/:id/audit`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        queried.push(params.get('eventType') ?? '');
        const items = params.get('eventType') === 'cp.gate.approved'
          ? [{ id: 'AUD-3', eventType: 'cp.gate.approved', operatorName: 'Ana', objectId: 'GT-1' }]
          : [];
        return HttpResponse.json({ items, total: items.length });
      }),
    );
    renderAt('/projects/PRJ-green-01/audit');
    // the workspace renders its children only once the project and workflow reads land
    await screen.findByText(/Verify chain integrity/i);
    await userEvent.type(inputFor(/^By event type$/), 'cp.gate.approved');
    await waitFor(() => expect(queried).toContain('cp.gate.approved'));
    expect(await screen.findByText('cp.gate.approved')).toBeInTheDocument();
  });

  it('passes the time window through rather than trimming the page', async () => {
    const queried: string[] = [];
    server.use(
      http.get(`/api/v1/projects/:id/audit`, ({ request }) => {
        queried.push(new URL(request.url).searchParams.get('occurredAfter') ?? '');
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );
    renderAt('/projects/PRJ-green-01/audit');
    // the workspace renders its children only once the project and workflow reads land
    await screen.findByText(/Verify chain integrity/i);
    await userEvent.type(inputFor(/^From$/), '2026-03-02T00:00');
    await waitFor(() => expect(queried.some((value) => value.startsWith('2026-03-02'))).toBe(true));
  });
});

describe('COMMIT trace (UI-OPS-002)', () => {
  it('reads the trace of one execution and names its artifacts', async () => {
    renderAt('/projects/PRJ-green-01/audit');
    // the workspace renders its children only once the project and workflow reads land
    await screen.findByText(/Verify chain integrity/i);
    await userEvent.type(inputFor(/Execution id/i), 'EX-9');
    await userEvent.click(screen.getByRole('button', { name: /Read trace/i }));
    expect(await screen.findByText(/env-dev/)).toBeInTheDocument();
    expect(await screen.findByText(/ART-PKG-1/)).toBeInTheDocument();
    expect(await screen.findByText(/ART-IMP-1/)).toBeInTheDocument();
  });

  it('says so when the execution id has no trace', async () => {
    renderAt('/projects/PRJ-green-01/audit');
    // the workspace renders its children only once the project and workflow reads land
    await screen.findByText(/Verify chain integrity/i);
    await userEvent.type(inputFor(/Execution id/i), 'EX-unknown');
    await userEvent.click(screen.getByRole('button', { name: /Read trace/i }));
    expect(await screen.findByText(/No trace for that execution id/i)).toBeInTheDocument();
  });
});
