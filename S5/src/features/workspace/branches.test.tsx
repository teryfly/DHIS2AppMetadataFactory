/*
 * Branch coverage for stage behaviours: MANUAL_CHECK handling (UI-WB-008) and the manual-authoring
 * JSON tab + patch submission feedback (UI-MAN-001..003). Uses MSW handler overrides to shape the
 * stage / allowed-actions without disturbing the shared fixtures.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import { WORKFLOW_ID, DESIGN_PROJECT_ID, gate1 } from '@/mocks/db';

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

describe('MANUAL_CHECK handling (UI-WB-008)', () => {
  it('offers pass/fail with a note when the stage needs a manual check', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/stages`, () =>
        HttpResponse.json({
          items: [
            { id: 'ST-manual', stageType: 'BUILD', status: 'MANUAL_CHECK', attempt: 1, artifactCount: 0, openQuestionCount: 0, highOpenQuestionCount: 0, blockingIssueCount: 0 },
          ],
          total: 1,
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-manual`);
    expect((await screen.findAllByText(/needs a manual check|需要人工核对/i)).length).toBeGreaterThan(0);
    expect(await screen.findByRole('button', { name: /PASSED|通过/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /FAILED|失败/ })).toBeInTheDocument();
  }, 15000);
});

describe('Manual authoring JSON tab (UI-MAN-002)', () => {
  it('switches to JSON and submits a patch, showing assigned UIDs', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          workflowId: WORKFLOW_ID,
          allowed: [
            { action: 'patch.submit', available: true, targetId: 'ST-req' },
            { action: 'stage.submit_for_approval', available: true, targetId: 'ST-req' },
          ],
          unavailable: [],
        }),
      ),
      http.get(`/api/v1/workflows/:id/gates`, () => HttpResponse.json({ items: [gate1], total: 1 })),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await userEvent.click(await screen.findByRole('button', { name: /Manual authoring/i }));
    // Switch to the JSON tab.
    await userEvent.click(await screen.findByText('JSON editor'));
    const submit = (await screen.findByRole('button', { name: /Submit patch/ })) as HTMLButtonElement;
    await waitForEnabled(submit);
    await userEvent.click(submit);
    expect(await screen.findByText(/UIDs assigned by this patch|新分配的 UID/i)).toBeInTheDocument();
  }, 15000);
});

async function waitForEnabled(el: HTMLButtonElement, ms = 3000) {
  const start = Date.now();
  while (el.disabled && Date.now() - start < ms) {
    await new Promise((r) => setTimeout(r, 50));
  }
}

/*
 * UI-WB-004: the switcher had five tabs and four of them could only answer "not implemented".
 * Graph is the one that has its own read and is small enough to carry here; the registry table and
 * the version diff live on the metadata screen, and a preview has no endpoint at all.
 */
describe('Stage view switcher (UI-WB-004)', () => {
  it('draws the dependency graph on the stage and offers no tab that cannot answer', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await userEvent.click(await screen.findByRole('button', { name: /Manual authoring/i }));
    await screen.findByRole('button', { name: /Submit patch/ });
    await userEvent.click(screen.getByText('Graph'));

    const list = await screen.findByRole('region', { name: 'Objects in this graph' });
    expect(within(list).getByText('entity:facility')).toBeInTheDocument();
    expect(screen.queryByText('Preview')).not.toBeInTheDocument();
  }, 15000);

  it('sends the operator to the metadata screen for the registry rather than copying it', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await userEvent.click(await screen.findByRole('button', { name: /Manual authoring/i }));
    await screen.findByRole('button', { name: /Submit patch/ });
    await userEvent.click(screen.getByText('Registry'));
    expect(await screen.findByRole('heading', { name: /Metadata/ })).toBeInTheDocument();
  }, 15000);
});
