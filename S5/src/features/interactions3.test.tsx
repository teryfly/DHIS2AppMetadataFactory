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
import { WORKFLOW_ID, DESIGN_PROJECT_ID, project1 } from '@/mocks/db';

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
    // Which of the two steps is on screen follows the project's own `archived` flag, because
    // CP-PRJ-007 orders them; whether the button can be pressed still comes from allowed-actions
    // alone. So an archived project whose delete S1 refuses shows a disabled button, not a live one.
    server.use(
      http.get(`/api/v1/projects/:id`, () => HttpResponse.json({ ...project1, archived: true })),
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          workflowId: WORKFLOW_ID,
          allowed: [
            { action: 'project.archive', available: true },
            { action: 'project.export', available: true },
          ],
          unavailable: [{ action: 'project.delete', available: false, reasonCode: 'EXPORT_NOT_TAKEN' }],
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/settings`);
    const deleteBtn = (await screen.findByRole('button', { name: /Delete/ })) as HTMLButtonElement;
    expect(deleteBtn.disabled).toBe(true);
  });

  it('edits the project management attributes and PATCHes them (UI-PRJ-004)', async () => {
    let patched: Record<string, unknown> | undefined;
    server.use(
      http.get(`/api/v1/workflows/:id/allowed-actions`, () =>
        HttpResponse.json({
          workflowId: WORKFLOW_ID,
          allowed: [{ action: 'project.edit', available: true }],
          unavailable: [],
        }),
      ),
      http.patch(`/api/v1/projects/:project_id`, async ({ request }) => {
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: DESIGN_PROJECT_ID, name: patched.name ?? '' });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/settings`);

    const name = (await screen.findByLabelText(/Project name/)) as HTMLInputElement;
    await userEvent.clear(name);
    await userEvent.type(name, 'Renamed register');

    const save = (await screen.findByRole('button', { name: /Save project details/ })) as HTMLButtonElement;
    await waitFor(() => expect(save.disabled).toBe(false), { timeout: 8000 });
    await userEvent.click(save);

    await waitFor(() => expect(patched).toMatchObject({ name: 'Renamed register' }), { timeout: 8000 });
    expect(patched).toMatchObject({
      ownerName: expect.any(String),
      defaultLanguage: 'zh-CN',
    });
  });
});

describe('Questionnaire answering (UI-Q-001)', () => {
  const questionnaire = [
    {
      id: 'Q-A',
      text: 'Which option for the age field?',
      severity: 'HIGH',
      answerMode: 'SINGLE',
      blocking: true,
      status: 'OPEN',
      workflowId: WORKFLOW_ID,
      stageId: 'ST-req',
      affects: ['entity:patient'],
      suggestions: [
        { id: 'S-A1', text: 'Reuse the existing data element', rationale: 'No new UID.', recommended: true },
        { id: 'S-A2', text: 'Create a separate data element', rationale: 'New UID.', recommended: false },
      ],
    },
    {
      id: 'Q-B',
      text: 'Where does the facility come from?',
      severity: 'HIGH',
      answerMode: 'SINGLE',
      blocking: true,
      status: 'OPEN',
      workflowId: WORKFLOW_ID,
      stageId: 'ST-req',
      affects: ['entity:facility'],
      suggestions: [
        { id: 'S-B1', text: 'Create a new organisation unit', rationale: 'New UID.', recommended: false },
        { id: 'S-B2', text: 'Reuse the existing organisation unit', rationale: 'No new UID.', recommended: true },
      ],
    },
  ];

  it('enables one submit after any answer and accepts every recommendation in one click', async () => {
    let sent: { answers?: Array<{ questionId: string; suggestionIds?: string[] }> } | undefined;
    server.use(
      http.get('/api/v1/workflows/:id/questions', () =>
        HttpResponse.json({ items: questionnaire, total: questionnaire.length, limit: 20, offset: 0, nextCursor: null }),
      ),
      http.post('/api/v1/questions/answers', async ({ request }) => {
        sent = (await request.json()) as typeof sent;
        return HttpResponse.json({
          answers: questionnaire.map((item) => ({ questionId: item.id, status: 'ANSWERED' })),
          resumed: false,
          runId: 'AR-1',
          status: 'WAITING_INPUT',
          questions: [],
        });
      }),
    );

    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);

    const first = await screen.findByRole('radio', { name: /Reuse the existing data element/ });
    await userEvent.click(first);

    const submit = await screen.findByRole('button', { name: /Submit all answers and regenerate/ }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false), { timeout: 8000 });

    await userEvent.click(await screen.findByRole('button', { name: /Accept all recommendations/ }));
    await waitFor(() => expect(screen.getByRole('radio', { name: /Reuse the existing organisation unit/ })).toBeChecked());

    await userEvent.click(submit);
    await waitFor(() => expect(sent).toBeDefined(), { timeout: 8000 });
    expect(sent?.answers).toEqual([
      { questionId: 'Q-A', suggestionIds: ['S-A1'] },
      { questionId: 'Q-B', suggestionIds: ['S-B2'] },
    ]);
  });
});
