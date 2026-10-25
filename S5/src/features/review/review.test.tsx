/*
 * UI-REV-001 / 003 / 005: artifact review, metadata review and the validation issue list. These
 * are the screens an operator uses to decide whether the factory's output may go to an
 * environment, so what they show must be S1's answer — including the cases where the honest
 * answer is "the bytes are gone" or "the profile is stale".
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import { DESIGN_PROJECT_ID } from '@/mocks/db';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <OperatorGate>
          <AppRoutes />
        </OperatorGate>
      </MemoryRouter>,
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('Artifact review (UI-REV-001)', () => {
  it('shows the artifact record: type, version, hash, creator and classification', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts`);
    // the list button is the unique handle; the same type string also appears in the detail panel
    await userEvent.click(await screen.findByRole('button', { name: /Validation report/ }));
    expect(await screen.findByText(/sha256:val1/)).toBeInTheDocument();
    expect(screen.getByText(/Blueprint compilation \/ Ana/)).toBeInTheDocument();
    expect(screen.getByText(/Internal/)).toBeInTheDocument();
  });

  it('says the content was purged instead of showing an empty box', async () => {
    server.use(
      http.get(`/api/v1/workflows/:id/artifacts`, () =>
        HttpResponse.json({ items: [{ id: 'ART-PHI-1', type: 'BUSINESS_INPUT', version: 1, status: 'APPROVED', size: 4096, dataClassification: 'PHI_PII', contentPurged: true, contentHash: 'sha256:phi1', creatorSubsystem: 'S1', creatorOperator: 'Ana' }], total: 1 }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts`);
    expect(await screen.findByText(/Content purged/i)).toBeInTheDocument();
    expect(screen.getByText(/bytes were cleared/i)).toBeInTheDocument();
    // Preview does not repeat it: one fact, one sentence on screen.
    expect(screen.getAllByText(/bytes were cleared/i)).toHaveLength(1);
  });

  it('reproduces and reports the hash comparison', async () => {
    let called = 0;
    server.use(
      http.post(`/api/v1/artifacts/:artifactId/reproduce`, () => {
        called += 1;
        return HttpResponse.json({ hash: 'sha256:pkg3', matches: true });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts`);
    await userEvent.click(await screen.findByRole('button', { name: /Reproduce/i }));
    await waitFor(() => expect(called).toBe(1));
    expect(await screen.findByText(/Reproduced: same hash/i)).toBeInTheDocument();
  });

  it('lists the sources and selects the artifact they came from', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts`);
    await userEvent.click(await screen.findByRole('tab', { name: 'Dependencies' }));
    const source = await screen.findByRole('button', { name: /ART-VAL-1/ });
    await userEvent.click(source);
    expect(await screen.findByText(/sha256:val1/)).toBeInTheDocument();
  });
});

describe('Validation issues (UI-REV-005)', () => {
  it('filters on the server and shows only the matching issues', async () => {
    const levels: (string | null)[] = [];
    server.use(
      http.get(`/api/v1/workflows/:id/validation-issues`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        levels.push(params.get('ruleId'));
        const all = [
          { issueId: 'ISS-1', level: 'BLOCKING', description: 'shortName is missing', ruleId: 'BPV-ST-004', businessKey: 'entity:facility', sourceStage: 'DOMAIN', artifactId: 'ART-VAL-1' },
          { issueId: 'ISS-2', level: 'WARNING', description: 'aggregationType not supported', ruleId: 'BPV-XA-002', businessKey: 'rule:age-range', sourceStage: 'MAPPING', artifactId: 'ART-VAL-1' },
        ];
        const rule = params.get('ruleId');
        const items = rule ? all.filter((i) => i.ruleId === rule) : all;
        return HttpResponse.json({ items, total: items.length });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/issues`);
    const table = (await screen.findByText('shortName is missing')).closest('table') as HTMLElement;
    expect(within(table).getByText('aggregationType not supported')).toBeInTheDocument();


    const rule = (await screen.findByLabelText(/^Rule$/)) as HTMLInputElement;
    await userEvent.type(rule, 'BPV-XA-002');
    await waitFor(() => expect(levels).toContain('BPV-XA-002'));
    // the row the rule matches stays, the other one is gone: proof the filter reached S1
    expect(await screen.findByText('aggregationType not supported')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('shortName is missing')).not.toBeInTheDocument());
  });

  it('jumps to the stage that produced the issue', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/issues`);
    const stageLink = await screen.findByRole('link', { name: 'Domain model' });
    expect(stageLink).toHaveAttribute('href', expect.stringContaining('/stages/'));
  });

});

describe('Metadata review (UI-REV-003)', () => {
  it('shows registry rows with disposition, UID and the decision reference', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/metadata`);
    expect(await screen.findByText('entity:facility')).toBeInTheDocument();
    expect(await screen.findByText('a1b2c3d4e5f60718')).toBeInTheDocument();
  });

  it('reports the newest run statistics', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/metadata`);
    await screen.findByText(/Changes in the newest run/i);
    expect(await screen.findByText('+ 5')).toBeInTheDocument();
    expect(await screen.findByText('~ 4')).toBeInTheDocument();
  });

  it('warns when the capability profile of the target is not fresh', async () => {
    server.use(
      http.get(`/api/v1/deployment-runs/:id/summary`, () =>
        HttpResponse.json({ runId: 'RUN-1', status: 'VALIDATED', environmentKey: 'env-dev', changeStats: { create: 1, update: 0, delete: 0 }, capabilityFreshness: { state: 'STALE', probedAt: '2026-01-01T00:00:00Z' } }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/metadata`);
    expect(await screen.findByText(/profile for this environment is STALE/i)).toBeInTheDocument();
  });

  it('groups the same objects by type when the view is switched', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/metadata`);
    await screen.findByText(/By registry/i);
    await userEvent.click(screen.getByText('By object type'));
    const table = (await screen.findByRole('columnheader', { name: 'Objects' })).closest('table') as HTMLElement;
    expect(within(table).getByText('organisationUnit')).toBeInTheDocument();
  });
});

describe('Metadata review by file (UI-REV-003, S5IF-BP17)', () => {
  it('lists the compiled files with their role and hash, and reads a missing count as no object', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/metadata`);
    await screen.findByText(/By registry/i);
    await userEvent.click(screen.getByText('By file'));

    const grid = await screen.findByRole('region', { name: 'By file' });
    expect(within(grid).getByText('metadata.json')).toBeInTheDocument();
    expect(within(grid).getByText('views/organisationUnits.json')).toBeInTheDocument();
    expect(within(grid).getByText('sha256:pay1')).toBeInTheDocument();
    expect(await screen.findByText(new RegExp(`sha256:pkg3`))).toBeInTheDocument();
    // README.md holds no object: the row says "—", which is not the same claim as 0
    const readme = within(grid).getByText('README.md').closest('tr') as HTMLElement;
    expect(within(readme).getAllByText('—').length).toBeGreaterThan(0);
    expect(within(readme).queryByText('0')).not.toBeInTheDocument();
  });

  it('says nothing was compiled instead of showing an empty package', async () => {
    renderAt('/projects/PRJ-brown-02/metadata');
    await screen.findByText(/By registry/i);
    await userEvent.click(screen.getByText('By file'));

    expect(await screen.findByText('Nothing compiled yet')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'By file' })).not.toBeInTheDocument();
  });

  it('asks S1 for the file list only when the view is open', async () => {
    let calls = 0;
    server.use(
      http.get('/api/v1/projects/:id/package-files', () => {
        calls += 1;
        return HttpResponse.json({ projectId: DESIGN_PROJECT_ID, files: [], kind: 'NONE', total: 0 });
      }),
    );

    renderAt(`/projects/${DESIGN_PROJECT_ID}/metadata`);
    await screen.findByText(/By registry/i);
    expect(calls).toBe(0);

    await userEvent.click(screen.getByText('By file'));
    await waitFor(() => expect(calls).toBeGreaterThan(0));
  });
});
