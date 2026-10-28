/*
 * The delete/archive affordance follows the project's state (CP-PRJ-007): a live project is
 * offered 归档, and only an archived one is offered 删除. Neither is a URL that pops the
 * irreversible confirmation on arrival — `?delete=1` used to do exactly that, over a page whose
 * sticky tab strip painted straight through the dialog.
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
import { project1, WORKFLOW_ID, DESIGN_PROJECT_ID } from '@/mocks/db';

const P = '/api/v1';

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

const archivedProject = { ...project1, id: 'PRJ-retired', name: 'Retired Register', archived: true };

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('Projects list footer (UI-PRJ-001)', () => {
  beforeEach(() => {
    server.use(
      http.get(`${P}/projects`, ({ request }) => {
        const showArchived = new URL(request.url).searchParams.get('includeArchived') === 'true';
        const items = showArchived ? [project1, archivedProject] : [project1];
        return HttpResponse.json({ items, total: items.length });
      }),
    );
  });

  it('offers 归档 rather than 删除 while the project is live, and never a delete query link', async () => {
    const { container } = renderAt('/projects');
    const card = await screen.findByText('Community Health Register');
    const footer = card.closest('.project-card') as HTMLElement;
    expect(within(footer).getByRole('link', { name: 'Archive' })).toBeInTheDocument();
    expect(within(footer).queryByRole('link', { name: 'Delete' })).toBeNull();
    expect(container.querySelector('a[href*="delete=1"]')).toBeNull();
  });

  it('offers 删除 once the project is archived', async () => {
    renderAt('/projects');
    await screen.findByText('Community Health Register');
    await userEvent.click(screen.getByRole('checkbox', { name: /Show archived/ }));
    const card = await screen.findByText('Retired Register');
    const footer = card.closest('.project-card') as HTMLElement;
    expect(within(footer).getByRole('link', { name: 'Delete' })).toBeInTheDocument();
    expect(within(footer).queryByRole('link', { name: 'Archive' })).toBeNull();
  });
});

describe('Project settings danger zone (UI-PRJ-004)', () => {
  it('does not open the delete confirmation from a query string', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/settings?delete=1`);
    await screen.findByRole('heading', { name: 'Project settings' });
    expect(screen.queryByText(/Delete this project/)).toBeNull();
  });

  it('says which half of the precondition is missing instead of offering deletion', async () => {
    server.use(
      http.get(`${P}/projects/:id`, () => HttpResponse.json(archivedProject)),
      http.get(`${P}/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          allowed: [{ action: 'project.export', available: true, details: {} }],
          unavailable: [
            {
              action: 'project.archive',
              available: false,
              reasonCode: 'PROJECT_ARCHIVED',
              details: {},
            },
            {
              action: 'project.delete',
              available: false,
              reasonCode: 'EXPORT_NOT_TAKEN',
              details: { requires: 'PROJECT_EXPORT' },
            },
          ],
          workflowId: WORKFLOW_ID,
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/settings`);
    expect(await screen.findByText(/has not been exported yet/)).toBeInTheDocument();
    // The button stays on screen and says why it cannot be used; hiding it would leave the
    // operator looking for a delete that the server has not refused for the reason they think.
    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled();
  });
});
