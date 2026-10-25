/*
 * The workflow-level controls (UI-WB-006 / FR-UI-009) and the agent-run cancel (UI-WB-009). Both
 * were endpoints with no entry point: the screens showed the state but offered nothing to change
 * it. Availability comes from allowed-actions in every case, so these tests also pin the mock's
 * own honesty -- pause and resume are one switch, and only the frame that names a run can cancel
 * it.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useProgressStore } from '@/sse/progress';
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

const overview = `/projects/${db.DESIGN_PROJECT_ID}/overview`;
const stage = (stageId: string) => `/projects/${db.DESIGN_PROJECT_ID}/stages/${stageId}`;

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
  // the workflow row is mutable on purpose (the pause handlers write it), so each test starts clean
  db.workflow1.paused = false;
  db.workflow1.cancelled = false;
});
afterEach(() => {
  server.resetHandlers();
  useProgressStore.setState({ byWorkflow: {} });
});

describe('workflow pause / resume / cancel (UI-WB-006)', () => {
  it('pauses, and the control becomes the other half of the same switch', async () => {
    renderAt(overview);
    const pause = () => screen.getByRole('button', { name: /Pause workflow/ });
    await waitFor(() => expect(pause()).toBeEnabled());
    await userEvent.click(pause());

    expect(await screen.findByRole('button', { name: /Resume workflow/ })).toBeInTheDocument();
    // a paused workflow cannot be paused again -- S1 says so, and the screen follows
    expect(screen.queryByRole('button', { name: /Pause workflow/ })).not.toBeInTheDocument();
  });

  it('asks before cancelling, and only cancels on the confirmation', async () => {
    const paths: string[] = [];
    server.use(
      http.post(`/api/v1/workflows/:id/cancel`, ({ request }) => {
        paths.push(new URL(request.url).pathname);
        return HttpResponse.json({ workflowId: db.WORKFLOW_ID, cancelled: true });
      }),
    );
    renderAt(overview);
    const cancel = () => screen.getByRole('button', { name: /^Cancel workflow/ });
    await waitFor(() => expect(cancel()).toBeEnabled());

    await userEvent.click(cancel());
    expect(paths).toEqual([]);
    // the confirmation carries the same label, so the last one on the page is the dialog's
    const confirm = () => screen.getAllByRole('button', { name: /^Cancel workflow/ }).at(-1) as HTMLElement;
    await userEvent.click(await confirm());
    await waitFor(() => expect(paths).toEqual([`/api/v1/workflows/${db.WORKFLOW_ID}/cancel`]));
  });

  it('links the gate that is holding the workflow', async () => {
    renderAt(overview);
    const link = await screen.findByRole('link', { name: /Approval/ });
    expect(link).toHaveAttribute('href', `/projects/${db.DESIGN_PROJECT_ID}/gate/${db.gate1.id}`);
  });
});

describe('agent run cancel (UI-WB-009)', () => {
  function runningStage() {
    server.use(
      http.get(`/api/v1/workflows/:id/stages`, () =>
        HttpResponse.json({
          items: [
            { id: 'ST-domain', stageType: 'DOMAIN', status: 'RUNNING', attempt: 1, artifactCount: 0, openQuestionCount: 0, highOpenQuestionCount: 0, blockingIssueCount: 0 },
          ],
          total: 1,
        }),
      ),
    );
  }

  it('names the run the progress frame reported and cancels that one', async () => {
    runningStage();
    const cancelled: string[] = [];
    server.use(
      http.post(`/api/v1/workflows/:id/agent-runs/:runId/cancel`, ({ params }) => {
        cancelled.push(String(params.runId));
        return HttpResponse.json({ runId: String(params.runId), status: 'CANCELLED' });
      }),
    );
    act(() => useProgressStore.getState().upsert(`${db.WORKFLOW_ID}:agent:ST-domain`, {
      workflowId: db.WORKFLOW_ID,
      stageId: 'ST-domain',
      runId: 'AR-7',
      currentStep: 'mapping',
      fraction: 0.4,
      lastSeq: 1,
    }));

    renderAt(stage('ST-domain'));
    const cancel = () => screen.getByRole('button', { name: /Cancel agent run/ });
    await waitFor(() => expect(cancel()).toBeEnabled());
    await userEvent.click(cancel());
    // the dialog names the run, because two stages can be running at once
    expect(await screen.findByText(/AR-7/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole('button', { name: /Cancel agent run/ }).at(-1) as HTMLElement);
    await waitFor(() => expect(cancelled).toEqual(['AR-7']));
  });

  it('offers nothing to cancel while no frame has named a run', async () => {
    runningStage();
    act(() => useProgressStore.getState().upsert(`${db.WORKFLOW_ID}:agent:ST-domain`, {
      workflowId: db.WORKFLOW_ID,
      stageId: 'ST-domain',
      currentStep: 'mapping',
      lastSeq: 1,
    }));
    renderAt(stage('ST-domain'));
    expect(await screen.findByText(/Agent running/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Cancel agent run/ })).not.toBeInTheDocument();
  });
});
