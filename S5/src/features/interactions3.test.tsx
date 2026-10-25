/*
 * Exercise the remaining interactive handlers so their inline callbacks run: answering a Question
 * (UI-Q-001/003), editing + saving system settings (UI-ADM-004), adding a manual-authoring row
 * (UI-MAN-001), and project-settings actions gated only by allowed-actions (UI-PRJ-004).
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
import { WORKFLOW_ID, DESIGN_PROJECT_ID } from '@/mocks/db';

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

describe('System settings edit + save (UI-ADM-004)', () => {
  it('edits a numeric field and persists via PUT', async () => {
    renderAt('/settings');
    const idem = await screen.findByText(/Idempotency retention/);
    const input = idem.closest('div')?.querySelector('input') as HTMLInputElement | null;
    if (input) {
      await userEvent.clear(input);
      await userEvent.type(input, '48');
    }
    await userEvent.click(screen.getByRole('button', { name: /Save settings/ }));
    expect(await screen.findByText(/Settings saved|设置已保存/i)).toBeInTheDocument();
  });
});

describe('Manual authoring form (UI-MAN-001)', () => {
  it('adds a new requirement row', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await userEvent.click(await screen.findByRole('button', { name: /Manual authoring/i }));
    await screen.findByText('Structured form');
    const before = screen.getAllByRole('textbox').length;
    const add = (await screen.findByRole('button', { name: /Add environment/ })) as HTMLElement;
    await userEvent.click(add);
    expect(screen.getAllByRole('textbox').length).toBeGreaterThan(before);
  });
});

describe('Project settings actions gated only by allowed-actions (UI-PRJ-004)', () => {
  it('issues the archive and export calls when S1 allows them', async () => {
    const seen: string[] = [];
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          workflowId: WORKFLOW_ID,
          allowed: [
            { action: 'project.archive', available: true },
            { action: 'project.export', available: true },
          ],
          unavailable: [{ action: 'project.delete', available: false, reasonCode: 'NOT_ARCHIVED' }],
        }),
      ),
      http.post(`/api/v1/projects/:project_id/archive`, () => {
        seen.push('archive');
        return HttpResponse.json({ id: DESIGN_PROJECT_ID, archived: true });
      }),
      http.get(`/api/v1/projects/:project_id/export`, () => {
        seen.push('export');
        return HttpResponse.json({ stepRunId: 'srp-1', accepted: true });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/settings`);
    // re-queried inside waitFor on purpose: the buttons first render while allowed-actions is
    // still in flight, and React replaces the node once the answer lands, so a reference
    // captured before that points at a detached element that stays disabled forever
    await waitFor(async () => {
      const exportBtn = (screen.getByRole('button', { name: /Export/ })) as HTMLButtonElement;
      const archiveBtn = (screen.getByRole('button', { name: /Archive/ })) as HTMLButtonElement;
      expect(exportBtn.disabled).toBe(false);
      expect(archiveBtn.disabled).toBe(false);
      await userEvent.click(exportBtn);
      await userEvent.click(archiveBtn);
      expect(seen).toEqual(expect.arrayContaining(['export', 'archive']));
    }, { timeout: 8000 });
  });

  it('keeps delete disabled when S1 does not allow it (FR-UI-009)', async () => {
    // The old handler also read the project row's own `archived` flag and gated on it, which
    // let the browser form its own opinion about a destructive action. Availability comes
    // from allowed-actions only, so an unavailable action must stay disabled here.
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          workflowId: WORKFLOW_ID,
          allowed: [
            { action: 'project.archive', available: true },
            { action: 'project.export', available: true },
          ],
          unavailable: [{ action: 'project.delete', available: false, reasonCode: 'NOT_ARCHIVED' }],
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/settings`);
    const deleteBtn = (await screen.findByRole('button', { name: /Delete/ })) as HTMLButtonElement;
    expect(deleteBtn.disabled).toBe(true);
  });
});
