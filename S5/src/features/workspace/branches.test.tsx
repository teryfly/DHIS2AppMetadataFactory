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

describe('Manual authoring submits RFC 6902 operations (UI-MAN-002)', () => {
  it('seeds from the draft and submits a real operations patch, showing assigned UIDs', async () => {
    const bodies: Record<string, unknown>[] = [];
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
      http.get(`/api/v1/projects/:id/blueprint/entries`, () =>
        HttpResponse.json({
          projectId: DESIGN_PROJECT_ID,
          source: 'DRAFT',
          entries: [{ _section: 'requirements', _path: '/requirements/items/req-1', businessKey: 'req-1', text: 'record weight', priority: 'MUST' }],
          notFound: [],
        }),
      ),
      http.post(`/api/v1/stages/:id/patches`, async ({ request }) => {
        bodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ stageId: 'ST-req', patchStatus: 'ACCEPTED', assignedUids: { 'req-new': 'aa11bb22cc33dd44' }, draftHash: 'sha256:cd34', issues: [], questions: 0 });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await userEvent.click(await screen.findByRole('button', { name: /Manual authoring/i }));

    // The draft entry is read out, not a blank form: the seeded row shows its businessKey.
    expect(await screen.findByDisplayValue('req-1')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Add environment/i }));
    const submit = (await screen.findByRole('button', { name: /Submit patch/ })) as HTMLButtonElement;
    await waitForEnabled(submit);
    await userEvent.click(submit);
    expect(await screen.findByText(/UIDs assigned by this patch|新分配的 UID/i)).toBeInTheDocument();

    // The submitted body is S2's operation set, not a `{requirements:[...]}` content body.
    const body = bodies[0] as { patch: { operations: { op: string; path: string }[] } };
    expect(Array.isArray(body.patch.operations)).toBe(true);
    expect(body.patch.operations.some((o) => o.op === 'replace' && o.path === '/requirements/items')).toBe(true);
  }, 15000);
});

/*
 * UI-WB-007 / BPV-ENV-002: the freeze named a businessKey; the entry table must mark exactly that
 * row so the operator opens the manual editor on the right object rather than guessing.
 */
describe('Conflict highlight on the entry table', () => {
  it('marks the row the rejection named and surfaces its suggested action', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/stages`, () =>
        HttpResponse.json({
          items: [
            { id: 'ST-map', stageType: 'MAPPING', status: 'BLOCKED', attempt: 1, artifactCount: 0, openQuestionCount: 0, highOpenQuestionCount: 0, blockingIssueCount: 1 },
          ],
          total: 1,
        }),
      ),
      http.get(`/api/v1/stages/:id`, ({ request }) => {
        const id = request.url.split('/').pop();
        return HttpResponse.json({
          id, stageType: 'MAPPING', status: 'BLOCKED', attempt: 1, artifactCount: 0, openQuestionCount: 0, highOpenQuestionCount: 0, blockingIssueCount: 1,
          feedback: { kind: 'VALIDATION', reason: '1 blocking issue(s) prevent the draft from being promoted', sourceStage: 'MAPPING', issues: [{ issueId: 'bpis-1', ruleId: 'BPV-ENV-002', level: 'BLOCKING', businessKey: 'followup.template.dataStore', description: 'code is already used', suggestedAction: 'change generationPolicy.codePrefix' }] },
        });
      }),
      http.get(`/api/v1/projects/:id/blueprint/entries`, () =>
        HttpResponse.json({
          projectId: DESIGN_PROJECT_ID,
          source: 'DRAFT',
          entries: [
            { _section: 'registry', _path: '/registry/entries/followup.template.dataStore', businessKey: 'followup.template.dataStore', dhis2Type: 'dataStore', name: '随访模板' },
            { _section: 'registry', _path: '/registry/entries/patient.weight', businessKey: 'patient.weight', dhis2Type: 'dataElement', name: '体重' },
          ],
          notFound: [],
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-map`);

    // The entry table renders the clean row too; wait for it so the (only-in-table) query is loaded.
    const cleanCell = await screen.findByText('patient.weight');
    expect(cleanCell.closest('tr')?.className).not.toContain('conflict');

    const conflictRow = document.querySelector('tr.record-table__row--conflict');
    expect(conflictRow).not.toBeNull();
    expect(conflictRow?.textContent).toContain('followup.template.dataStore');
    expect(conflictRow?.textContent).toContain('change generationPolicy.codePrefix');
    // "Conflict" appears in the row Tag, the banner and the only-conflicts toggle.
    expect((await screen.findAllByText(/Conflict|冲突/)).length).toBeGreaterThan(0);
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
describe('Stage entries (UI-WB-004)', () => {
  it('reads the current draft, so a domain stage shows the model waiting for approval', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/stages`, () =>
        HttpResponse.json({
          items: [
            { id: 'ST-domain', stageType: 'DOMAIN', status: 'WAITING_APPROVAL', attempt: 1, artifactCount: 0, openQuestionCount: 0, highOpenQuestionCount: 0, blockingIssueCount: 0 },
          ],
          total: 1,
        }),
      ),
      http.get(`/api/v1/projects/:id/blueprint/entries`, ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('source')).toBe('DRAFT');
        expect(url.searchParams.getAll('sections')).toEqual(['domain', 'useCases', 'actors']);
        return HttpResponse.json({
          projectId: DESIGN_PROJECT_ID,
          source: 'DRAFT',
          entries: [
            { _section: 'domain', _path: '/domain/entities/patient', businessKey: 'patient', displayName: { default: '患者' }, attributes: [], source: {} },
            { _section: 'useCases', _path: '/useCases/items/patient.receive', businessKey: 'patient.receive', name: '接收出院患者', actors: ['followup.nurse'], source: {} },
          ],
          notFound: [],
        });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-domain`);

    expect(await screen.findByText('patient')).toBeInTheDocument();
    expect(await screen.findByText('接收出院患者')).toBeInTheDocument();
  }, 15000);
});

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
