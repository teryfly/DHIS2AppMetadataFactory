/*
 * UI-DEP-005: the commit outcome. The panel answers what happened (execution status, the
 * normalised issues), shows the ImportReport as DHIS2 returned it, and names the post-verification
 * difference and the snapshot it was taken against. Each document is read by its own artifact id
 * and only when opened — a screen that renders the wrong report's bytes looks exactly like the
 * right one until somebody reads it.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

function renderConsole(runId: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[`/projects/${db.DESIGN_PROJECT_ID}/deployments/${runId}`]}>
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

describe('Commit outcome (UI-DEP-005)', () => {
  it('says nothing until there is an execution to report on', async () => {
    const asked: string[] = [];
    server.use(
      http.get(`/api/v1/executions/:executionId`, ({ request }) => {
        asked.push(new URL(request.url).pathname);
        return HttpResponse.json({ executionId: 'EX-x', status: 'SUCCESS' });
      }),
    );

    renderConsole('RUN-1');
    await screen.findByRole('heading', { name: /Deployment console/ });
    expect(screen.queryByText('Execution outcome')).not.toBeInTheDocument();
    expect(asked).toEqual([]);
  });

  it('reports the execution, its normalised issue and the post-verification divergence', async () => {
    renderConsole('RUN-9');
    const panel = await screen.findByRole('region', { name: /Execution outcome/ });

    // the issue is one row of the list, so its own text carries the reason beside the key
    expect(await within(panel).findByText(/name collided with an existing element/)).toBeInTheDocument();
    expect(within(panel).getByText(/rename the element in the blueprint/)).toBeInTheDocument();
    expect(panel.textContent).toMatch(/diverged/);
    expect(panel.textContent).toMatch(/ART-SNP-1/);
  });

  it('reads each document by its own artifact id, and only once it is opened', async () => {
    const read: string[] = [];
    server.use(
      http.get(`/api/v1/artifacts/:artifactId/content`, ({ params }) => {
        read.push(String(params.artifactId));
        const body = db.artifactContents[String(params.artifactId)] ?? db.artifactContent;
        return new HttpResponse(body, { headers: { 'content-type': 'application/octet-stream' } });
      }),
    );

    renderConsole('RUN-9');
    const panel = within(await screen.findByRole('region', { name: /Execution outcome/ }));
    expect(read).toEqual([]);

    const importReport = await panel.findByRole('button', { name: /ImportReport/ });
    await userEvent.click(importReport);
    // the ImportReport's own document, not the execution report's -- and opening one block
    // reads nothing for the others (the bytes are large and the operator came for one report)
    expect(read).toEqual(['ART-IMP-1']);

    await userEvent.click(await panel.findByRole('button', { name: /Post-verification differences/ }));
    expect(read).toEqual(['ART-IMP-1', 'ART-PV-1']);
    expect(panel.getByRole('button', { name: /Execution report/ })).toBeInTheDocument();
    expect(read).toEqual(['ART-IMP-1', 'ART-PV-1']);
  });

  // UI-DEP-002: the run had a cancel endpoint and no way to reach it. FR-UI-009 decides who may
  // press it, so the same button is asked about in both states on purpose. The request itself --
  // verb, path, idempotency key -- is pinned at the mutation layer (api/mutations.test.tsx); what
  // this screen owes the operator is the answer appearing on it, which the mock's cancel writes
  // onto the same run row the summary reads.
  it('cancels the run S1 says may be cancelled, and shows the answer', async () => {
    renderConsole('RUN-1');
    // re-queried each check: React replaces the button as the gating answer arrives, and a
    // reference held from before then points at a detached node that stays disabled forever
    const cancel = () => screen.getByRole('button', { name: /Cancel run/ });
    await waitFor(() => expect(cancel()).toBeEnabled());
    await userEvent.click(cancel());
    await screen.findByText('Cancelled');
  });

  it('will not offer to cancel a run S1 did not list', async () => {
    // The list answers COMMIT for this run and says nothing about cancelling it. Waiting for the
    // first button to open proves the list arrived, so the second assertion measures a real
    // refusal rather than a query that had not resolved yet (FR-UI-009).
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          ...db.allowedActions1,
          allowed: [...(db.allowedActions1.allowed ?? []), { action: 'execution.commit', available: true, targetId: 'RUN-9' }],
        }),
      ),
    );
    renderConsole('RUN-9');
    const commit = () => screen.getByRole('button', { name: /^COMMIT$/ });
    await waitFor(() => expect(commit()).toBeEnabled());
    expect(screen.getByRole('button', { name: /Cancel run/ })).toBeDisabled();
  });
});
