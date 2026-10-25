/*
 * UI-REV-001 (an artifact has an address) and UI-IA-001's Handoff section (CP-UI-021).
 *
 * Both were "the data is there and the screen is not": the artifact detail was reachable only by
 * clicking a row in a component-local selection, so nothing could be linked or quoted, and the
 * development package had an endpoint that no console had ever called. What follows is the pair of
 * properties that make those statements checkable — an address that opens one artifact, and a page
 * that shows the package S2 produced or says which stage has to run first.
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
import { DESIGN_PROJECT_ID, HANDOFF_PROJECT_ID, workflowActionsView } from '@/mocks/db';

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

describe('an artifact has its own address (UI-REV-001)', () => {
  it('opens the named artifact from the URL alone, with nothing clicked', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts/ART-VAL-1`);
    // sha256:val1 belongs to ART-VAL-1 and to no other row's detail panel
    expect(await screen.findByText(/sha256:val1/)).toBeInTheDocument();
    const row = screen.getByRole('button', { name: /Validation report/ });
    expect(row).toHaveAttribute('aria-current', 'true');
  });

  it('keeps the address of the artifact it is showing where a reviewer can paste it', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts/ART-VAL-1`);
    const copy = await screen.findByRole('button', { name: /Copy link/i });
    // jsdom has no clipboard, so the fallback path is what runs here; the property under test is
    // that the affordance exists and is named, not that the OS accepted the write.
    await userEvent.click(copy);
    expect(await screen.findByText(/not allowed to copy|Link copied/i)).toBeInTheDocument();
  });

  it('follows a lineage jump into the address bar', async () => {
    // The fixture's lineage answer is ART-PKG-1 ← ART-VAL-1, so this is the whole round trip:
    // clicking a source has to move the address, not just the panel.
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts/ART-PKG-1`);
    await userEvent.click(await screen.findByRole('tab', { name: /Dependencies/i }));
    await userEvent.click(await screen.findByRole('button', { name: 'ART-VAL-1' }));

    expect(await screen.findByText(/sha256:val1/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Validation report/ })).toHaveAttribute('aria-current', 'true');
  });

  it('gives the artifact bytes a real download address S1 itself names (CP-UI-016)', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts/ART-VAL-1`);
    const dl = await screen.findByRole('link', { name: /Download/i });
    expect(dl).toHaveAttribute('href', '/api/v1/artifacts/ART-VAL-1/content?download=true');
  });

  it('offers no download when S1 withholds the action, and says why (UI-BR-01)', async () => {
    server.use(
      http.get('/api/v1/workflows/:id/allowed-actions', ({ params }) => {
        const view = workflowActionsView(String(params.id));
        return HttpResponse.json({
          ...view,
          allowed: (view.allowed ?? []).filter((entry) => entry.action !== 'artifact.download'),
          unavailable: [
            ...(view.unavailable ?? []),
            { action: 'artifact.download', available: false, reasonCode: 'PROJECT_ARCHIVED', details: {} },
          ],
        });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/artifacts/ART-VAL-1`);
    await screen.findByText(/sha256:val1/);
    expect(screen.queryByRole('link', { name: /Download/i })).not.toBeInTheDocument();
    expect(screen.getByText(/archived and read-only/i)).toBeInTheDocument();
  });
});

describe('Handoff: the development package (UI-IA-001, CP-UI-021)', () => {
  it('lists S2’s files with their kind, size and hash, and links the package artifact', async () => {
    renderAt(`/projects/${HANDOFF_PROJECT_ID}/handoff`);
    expect(await screen.findByText('src/api/dataElements.ts')).toBeInTheDocument();
    expect(screen.getByText('Contract')).toBeInTheDocument();
    expect(screen.getByText(/2\.4 KB/)).toBeInTheDocument();

    const link = screen.getByRole('link', { name: /ART-HOF-1/ });
    expect(link).toHaveAttribute('href', `/projects/${HANDOFF_PROJECT_ID}/artifacts/ART-HOF-1`);
  });

  it('asks S1 for the file before it shows it, and hands the browser a real download', async () => {
    let asked = '';
    server.use(
      http.get('/api/v1/projects/:id/frontend-package/files/*', ({ request }) => {
        asked = decodeURIComponent(new URL(request.url).pathname.split('/files/')[1] ?? '');
        return new HttpResponse('{"page":"patientForm"}\n', { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
      }),
    );
    renderAt(`/projects/${HANDOFF_PROJECT_ID}/handoff`);
    const row = await screen.findByText('src/ui/patientForm.json');
    // the actions live in their own cell, so the scope has to be the row
    const tr = row.closest('tr');
    if (!tr) throw new Error('the file path is not inside a table row');

    expect(asked).toBe('');
    await userEvent.click(within(tr).getByRole('button', { name: /View/i }));
    // The listing does not hold every file's bytes in advance, and the byte read is the thing that
    // makes "View" mean something. (What Monaco then draws is covered by the artifact page's tests.)
    await waitFor(() => expect(asked).toBe('src/ui/patientForm.json'));

    const downloads = screen.getAllByRole('link', { name: /Download/i });
    expect(downloads.length).toBeGreaterThan(0);
    expect(downloads[0]).toHaveAttribute('href', expect.stringContaining('download=true'));
  });

  it('says which stage produces the package, without asking for one the workflow has not delivered (UI-BR-01)', async () => {
    let asked = 0;
    server.use(
      http.get('/api/v1/projects/:id/frontend-package/files', () => {
        asked += 1;
        return HttpResponse.json({ projectId: 'PRJ-green-01', files: [] });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/handoff`);
    expect(await screen.findByText(/No development package yet/i)).toBeInTheDocument();
    expect(screen.getByText(/stage produces it/i)).toBeInTheDocument();
    expect(screen.queryByText(/src\/api\/dataElements\.ts/)).not.toBeInTheDocument();
    // A mode-A project's `allowed-actions` withholds the download with PACKAGE_NOT_GENERATED, and
    // firing the read anyway is what UI-BR-01 forbids: the page would be deciding for itself that
    // there is something to fetch.
    expect(asked).toBe(0);
  });

  it('still reads the guidance from S1 when the gate opens and the listing answers 404 (CP-UI-022)', async () => {
    let asked = 0;
    server.use(
      http.get('/api/v1/workflows/:id/allowed-actions', () =>
        HttpResponse.json({ scope: 'workflow', allowed: [{ action: 'frontend_package.download', available: true }], unavailable: [] }),
      ),
      http.get('/api/v1/projects/:id/frontend-package/files', () => {
        asked += 1;
        return HttpResponse.json(
          { errorCode: 'NOT_FOUND', message: 'no development package yet', details: { reason: 'NOT_GENERATED', requiredStage: 'HANDOFF' } },
          { status: 404 },
        );
      }),
    );
    // The stale-gate race is real: allowed-actions is cached, the artifact can be purged after it.
    renderAt(`/projects/${DESIGN_PROJECT_ID}/handoff`);
    expect(await screen.findByText(/No development package yet/i)).toBeInTheDocument();
    expect(asked).toBeGreaterThan(0);
  });

  it('is reachable from the workspace, not only from its URL', async () => {
    renderAt(`/projects/${HANDOFF_PROJECT_ID}/handoff`);
    const sections = await screen.findByRole('navigation', { name: /Project sections/i });
    // The API review sits under Results, which is the primary section that owns it; the link the
    // old flat rail carried is still one step away rather than orphaned again.
    expect(within(sections).getByRole('link', { name: 'Results' })).toHaveAttribute(
      'href',
      `/projects/${HANDOFF_PROJECT_ID}/artifacts`,
    );
    const views = screen.getByRole('navigation', { name: /Section views/i });
    expect(within(views).getByRole('link', { name: 'Handoff package' })).toBeInTheDocument();
  });
});
