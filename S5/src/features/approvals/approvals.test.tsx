/*
 * The approver's page (S5IF-FLD03 in the navigation). What this file is really guarding is one
 * sentence in the header: the list, its filter and its count come from S1, so a row the operator
 * cannot see here is a gate that never gets decided. Local filtering would do exactly that — it can
 * only see the page that arrived — so the tests assert the query parameters leave with the request.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('the approval queue at its own address (S5IF-FLD03)', () => {
  it('lists every gate that is stopped for a person, with the project it belongs to', async () => {
    renderAt('/approvals');
    // the project names are also options in the filter, so the row itself is what is being asked for
    await waitFor(() => expect(screen.getAllByText(db.project1.name ?? '', { selector: 'strong' })).toHaveLength(1));
    expect(screen.getAllByText(db.project2.name ?? '', { selector: 'strong' })).toHaveLength(1);
    // the honest count line: what is on screen and what the server says there is
    expect(screen.getByText(/2 of 2/)).toBeInTheDocument();
  });

  it('names a gate by the work it closes, not by the token the wire carries', async () => {
    renderAt('/approvals');
    await waitFor(() => expect(screen.getAllByText(db.project1.name ?? '', { selector: 'strong' })).toHaveLength(1));
    expect(screen.getByText(/Requirements/)).toBeInTheDocument();
    expect(screen.getByText(/Commit/)).toBeInTheDocument();
    expect(screen.queryByText(/REQUIREMENT_GATE|DEPLOY_GATE/)).not.toBeInTheDocument();
  });

  it('sends the server’s own filters, and resets the page when they change', async () => {
    const asked: string[] = [];
    server.use(
      http.get('/api/v1/gates', ({ request }) => {
        asked.push(new URL(request.url).search);
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );
    renderAt('/approvals');
    await waitFor(() => expect(asked.length).toBeGreaterThan(0));
    expect(asked[0]).toContain('status=WAITING');
    expect(asked[0]).not.toContain('mine=');

    await userEvent.click(screen.getByRole('checkbox', { name: /Awaiting my decision/i }));
    await waitFor(() => expect(asked[asked.length - 1]).toContain('mine=true'));

    await userEvent.selectOptions(screen.getByLabelText(/Filter by project/i), db.HANDOFF_PROJECT_ID);
    const last = asked[asked.length - 1];
    // a new filter is a new list: the request must not still be carrying an old offset
    expect(last).toContain(`projectId=${db.HANDOFF_PROJECT_ID}`);
    expect(last).toContain('offset=0');
  });

  it('links each row to the gate that decides it', async () => {
    renderAt('/approvals');
    const open = await screen.findAllByRole('link', { name: /Open to decide/i });
    expect(open.map((l) => l.getAttribute('href'))).toEqual([
      `/projects/${db.DESIGN_PROJECT_ID}/gate/GT-1`,
      `/projects/${db.HANDOFF_PROJECT_ID}/gate/GT-2`,
    ]);
  });

  it('says so when nothing is waiting, instead of showing an empty box', async () => {
    server.use(http.get('/api/v1/gates', () => HttpResponse.json({ items: [], total: 0 })));
    renderAt('/approvals');
    expect(await screen.findByText(/Nothing is waiting on a decision right now/i)).toBeInTheDocument();
  });

  it('shows a refused read as a refusal, not as an empty queue', async () => {
    server.use(http.get('/api/v1/gates', () => HttpResponse.json({ errorCode: 'INTERNAL', message: 'boom' }, { status: 500 })));
    renderAt('/approvals');
    expect(await screen.findByText(/boom|error/i)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing is waiting/i)).not.toBeInTheDocument();
  });

  it('is reachable from the main navigation', async () => {
    renderAt('/');
    const link = await screen.findByRole('link', { name: /Awaiting approval/i });
    expect(link).toHaveAttribute('href', '/approvals');
  });
});
