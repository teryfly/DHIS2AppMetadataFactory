/*
 * UI-ADM-001 / UI-ADM-002 on the environment form: it must actually submit, and it must refuse
 * locally what S1 refuses remotely — a literal credential, a production environment whose
 * discovery and import share one secret (DX-AC-02), a plain-http address under required TLS.
 * Blocking those here is what keeps the operator from meeting them as a 422 after a click.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { EnvironmentForm } from './EnvironmentsView';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

function renderForm(props: Parameters<typeof EnvironmentForm>[0]) {
  return render(
    <AppProviders>
      <MemoryRouter>
        <EnvironmentForm {...props} />
      </MemoryRouter>
    </AppProviders>,
  );
}

/** Indexed queries are `| undefined` under this tsconfig; this names the intent once. */
function nthLabel(label: RegExp, index: number): HTMLElement {
  const found = screen.getAllByLabelText(label)[index];
  if (!found) throw new Error(`no element for ${String(label)} at index ${index}`);
  return found;
}

// The page swaps its Loading tree for the real one, and a node held across that swap is
// detached with whatever `disabled` it had then — so every gate check re-queries.
function nthButton(name: RegExp, index = 0): HTMLButtonElement {
  const found = (screen.getAllByRole('button', { name }) as HTMLButtonElement[])[index];
  if (!found) throw new Error(`no button matching ${String(name)} at index ${index}`);
  return found;
}

async function enabledButton(name: RegExp, index = 0): Promise<HTMLButtonElement> {
  await waitFor(() => expect(nthButton(name, index).disabled).toBe(false));
  return nthButton(name, index);
}

const save = () => screen.getByRole('button', { name: /Save environment/ }) as HTMLButtonElement;

beforeEach(() => useOperatorStore.getState().setName('Ana'));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

describe('creating an environment (UI-ADM-001)', () => {
  it('posts the record with env:// references and reports success', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/environments', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ environmentKey: 'clinic-dev' }, { status: 201 });
      }),
    );
    const onSaved = vi.fn();
    renderForm({ environment: null, onClose: vi.fn(), onSaved });

    await userEvent.type(screen.getByLabelText(/^Environment key/), 'clinic-dev');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'https://clinic-dev.invalid');
    await userEvent.selectOptions(screen.getByLabelText(/^Class/), 'DEV');
    await userEvent.type(screen.getByLabelText(/^Version constraint/), '2.41.x');
    await userEvent.type(nthLabel(/^Secret reference/, 0), 'env://CLINIC_DISCOVERY');
    await userEvent.type(nthLabel(/^Secret reference/, 1), 'env://CLINIC_IMPORT');

    await waitFor(() => expect(save().disabled).toBe(false));
    await userEvent.click(save());

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({
      environmentKey: 'clinic-dev',
      baseUrl: 'https://clinic-dev.invalid',
      environmentClass: 'DEV',
      authMode: 'PAT',
      versionConstraint: '2.41.x',
    });
    expect((captured.body?.credentials as Record<string, unknown>[]).map((c) => c.secretRef)).toEqual([
      'env://CLINIC_DISCOVERY',
      'env://CLINIC_IMPORT',
    ]);
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  });

  it('refuses a literal key before any request is made (UI-ADM-002)', async () => {
    let asked = 0;
    server.use(
      http.post('/api/v1/environments', () => {
        asked += 1;
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    renderForm({ environment: null, onClose: vi.fn() });
    await userEvent.type(screen.getByLabelText(/^Environment key/), 'env-literal');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'https://x.invalid');
    await userEvent.type(nthLabel(/^Secret reference/, 0), 'sk-realmagic');

    expect(await screen.findAllByText(/reference, never a value/i)).not.toHaveLength(0);
    // a disabled submit cannot be clicked at all, which is the assertion that matters here
    expect(save().disabled).toBe(true);
    expect(asked).toBe(0);
  });

  it('refuses a plain-http address while TLS is required', async () => {
    renderForm({ environment: null, onClose: vi.fn() });
    await userEvent.type(screen.getByLabelText(/^Environment key/), 'env-insecure');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'http://insecure.invalid');

    expect(await screen.findByText(/must start with https:/i)).toBeInTheDocument();
    expect(save().disabled).toBe(true);
  });

  it('requires two different references from a production environment (DX-AC-02)', async () => {
    renderForm({ environment: null, onClose: vi.fn() });
    await userEvent.type(screen.getByLabelText(/^Environment key/), 'env-prod-ui');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'https://prod.invalid');
    await userEvent.selectOptions(screen.getByLabelText(/^Class/), 'PRODUCTION');
    await userEvent.type(screen.getByLabelText(/^Version constraint/), '2.41.x');
    await userEvent.type(nthLabel(/^Secret reference/, 0), 'env://SHARED_TOKEN');
    await userEvent.type(nthLabel(/^Secret reference/, 1), 'env://SHARED_TOKEN');

    expect(await screen.findByText(/needs both a discovery and an import reference/i)).toBeInTheDocument();
    expect(save().disabled).toBe(true);

    await userEvent.clear(nthLabel(/^Secret reference/, 1));
    await userEvent.type(nthLabel(/^Secret reference/, 1), 'env://PROD_IMPORT');
    // still refused: a production environment with no commit window is not a production
    // environment at all (DX-ENV-002), and S1 answers that with a 422, not a warning
    expect(await screen.findByText(/at least one window/i)).toBeInTheDocument();
    expect(save().disabled).toBe(true);

    await userEvent.click(screen.getByRole('button', { name: /Add window/ }));
    expect(save().disabled).toBe(true); // a window with no weekday is not a window
    await userEvent.click(screen.getByRole('checkbox', { name: 'Mon' }));
    await waitFor(() => expect(save().disabled).toBe(false));
  });

  it('refuses an address with no scheme at all, whether or not TLS is required', async () => {
    renderForm({ environment: null, onClose: vi.fn() });
    await userEvent.type(screen.getByLabelText(/^Environment key/), 'env-noscheme');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'prod.invalid');
    await userEvent.click(screen.getByLabelText(/^Require TLS/));

    expect(await screen.findByText(/must start with http:\/\/ or https:\/\//i)).toBeInTheDocument();
    expect(save().disabled).toBe(true);
  });

  /*
   * DX-ENV-002 is the rule that makes a production environment a production environment, and S4
   * stores the whole list rather than merging into it — so what the operator builds here is
   * exactly what the deployment commits to.
   */
  it('builds the commit window a production environment is registered with', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/environments', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ environmentKey: 'env-prod-ui' }, { status: 201 });
      }),
    );
    renderForm({ environment: null, onClose: vi.fn() });
    await userEvent.type(screen.getByLabelText(/^Environment key/), 'env-prod-ui');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'https://prod.invalid');
    await userEvent.selectOptions(screen.getByLabelText(/^Class/), 'PRODUCTION');
    await userEvent.type(screen.getByLabelText(/^Version constraint/), '2.41.x');
    await userEvent.type(nthLabel(/^Secret reference/, 0), 'env://P_DISC');
    await userEvent.type(nthLabel(/^Secret reference/, 1), 'env://P_IMPORT');
    await userEvent.click(screen.getByRole('button', { name: /Add window/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Wed' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Sat' }));

    // a clock the server cannot read is refused here, where the box is still on screen
    const start = screen.getByLabelText(/^From/);
    await userEvent.clear(start);
    await userEvent.type(start, '2am');
    expect(await screen.findByText(/use a 24-hour time/i)).toBeInTheDocument();
    expect(save().disabled).toBe(true);
    await userEvent.clear(start);
    await userEvent.type(start, '02:15');
    await userEvent.clear(screen.getByLabelText(/^To/));
    await userEvent.type(screen.getByLabelText(/^To/), '05:00');

    // the second window the operator adds and then removes must not reach the body
    await userEvent.click(screen.getByRole('button', { name: /Add window/ }));
    await userEvent.click(nthButton(/Remove window/, 1));
    expect(screen.getAllByRole('checkbox', { name: 'Wed' })).toHaveLength(1);

    await waitFor(() => expect(save().disabled).toBe(false));
    await userEvent.click(save());

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body?.protection).toMatchObject({
      timezone: 'UTC',
      commitWindows: [{ days: [3, 6], start: '02:15', end: '05:00' }],
    });
  });

  it('adds and removes credential rows', async () => {
    renderForm({ environment: null, onClose: vi.fn() });
    expect(screen.getAllByLabelText(/^Secret reference/)).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: /Add credential/ }));
    expect(screen.getAllByLabelText(/^Secret reference/)).toHaveLength(3);
    await userEvent.selectOptions(nthLabel(/^Credential scope/, 0), 'import');
    expect((nthLabel(/^Credential scope/, 0) as HTMLSelectElement).value).toBe('import');
    await userEvent.click(nthButton(/Remove/));
    expect(screen.getAllByLabelText(/^Secret reference/)).toHaveLength(2);
  });
});

describe('editing an environment (UI-ADM-001)', () => {
  it('keeps the key read-only and sends a patch, not a replacement', async () => {
    const existing = db.state.environments.find((e) => e.environmentKey === 'env-dev');
    expect(existing).toBeTruthy();
    let body: Record<string, unknown> | null = null;
    let verb = '';
    server.use(
      http.patch('/api/v1/environments/env-dev', async ({ request }) => {
        verb = request.method;
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...existing, timeoutSeconds: 45 });
      }),
    );
    renderForm({ environment: existing ?? null, onClose: vi.fn() });

    const key = screen.getByLabelText(/^Environment key/) as HTMLInputElement;
    expect(key.disabled).toBe(true);
    expect(key.value).toBe('env-dev');
    // the stored references come back prefilled, so an edit never has to retype them
    expect(nthLabel(/^Secret reference/, 0)).toHaveValue('vault://dhis2/dev/discovery');

    const timeoutField = screen.getByLabelText(/^Timeout/);
    await userEvent.clear(timeoutField);
    await userEvent.type(timeoutField, '45');
    await waitFor(() => expect(save().disabled).toBe(false));
    await userEvent.click(save());

    await waitFor(() => expect(body).not.toBeNull());
    expect(verb).toBe('PATCH');
    expect(body).not.toHaveProperty('environmentKey');
    expect(body).toMatchObject({ timeoutSeconds: 45 });
    // nothing the operator did not touch is resent — PATCH reads an absent field as "unchanged"
    expect(body).not.toHaveProperty('baseUrl');
    expect(body).not.toHaveProperty('credentials');
  });

  it('will not offer the class as a patchable field (DX-ENV-001)', async () => {
    const existing = db.state.environments.find((e) => e.environmentKey === 'env-dev');
    let body: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/v1/environments/env-dev', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(existing);
      }),
    );
    renderForm({ environment: existing ?? null, onClose: vi.fn() });

    const select = screen.getByLabelText(/^Class/) as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.value).toBe('TEST');
    expect(screen.getByText(/chosen at registration/i)).toBeInTheDocument();

    const timeoutField = screen.getByLabelText(/^Timeout/);
    await userEvent.clear(timeoutField);
    await userEvent.type(timeoutField, '75');
    await userEvent.click(save());
    await waitFor(() => expect(body).not.toBeNull());
    // a class the form cannot change is not in the body either: S1 answers one with a 422
    expect(body).not.toHaveProperty('environmentClass');
    expect(body).toMatchObject({ timeoutSeconds: 75 });
  });

  it('sends an explicit empty string so a cleared note actually clears', async () => {
    const existing = db.state.environments.find((e) => e.environmentKey === 'env-dev');
    const notes = (existing?.notes ?? '') + 'keep me';
    const stored = { ...existing, notes } as NonNullable<typeof existing>;
    let body: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/v1/environments/env-dev', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...stored, notes: '' });
      }),
    );
    renderForm({ environment: stored, onClose: vi.fn() });
    await userEvent.clear(screen.getByLabelText(/^Notes/));
    // timeout is otherwise untouched, so an emptied Notes box must be the only field sent
    await waitFor(() => expect(save().disabled).toBe(false));
    await userEvent.click(save());
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({ notes: '' });
  });
});

/*
 * The page's two other write paths, both gated by S1's platform answer: a connection test
 * rendered from the real check shape, and an emergency lock S1 refuses without a reason.
 */
describe('the environments page (UI-ADM-001)', () => {
  function renderPage() {
    return render(
      <AppProviders>
        <MemoryRouter initialEntries={['/environments']}>
          <AppRoutes />
        </MemoryRouter>
      </AppProviders>,
    );
  }

  it('opens the row editor with the stored values and closes on cancel without writing', async () => {
    let written = 0;
    server.use(
      http.patch('/api/v1/environments/env-dev', () => {
        written += 1;
        return HttpResponse.json({});
      }),
    );
    renderPage();
    await userEvent.click(await enabledButton(/^Edit$/, 0));

    const key = screen.getByLabelText(/^Environment key/) as HTMLInputElement;
    expect(key.value).toBe('env-dev');
    expect(key.disabled).toBe(true);
    expect(nthLabel(/^Secret reference/, 0)).toHaveValue('vault://dhis2/dev/discovery');

    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    expect(screen.queryByLabelText(/^Environment key/)).not.toBeInTheDocument();
    expect(written).toBe(0);
  });

  it('renders the checks in S1 spelling rather than an invented one', async () => {
    renderPage();
    await userEvent.click(await enabledButton(/Test connection/));

    // `state: 'PASSED'` and a `missingAuthorities` map — mock and server agree here, on S1's own
    // eight check names (DX-CON-001), which is the whole point of asserting them verbatim.
    expect(await screen.findByText(/Connection test/i)).toBeInTheDocument();
    expect(screen.getByText(/addressReachable — Passed/i)).toBeInTheDocument();
    expect(screen.getByText(/metadataImport — Failed/i)).toBeInTheDocument();
    // a probe that did not run at all is a third state, and reading it as a failure would send
    // the operator to fix something that was never broken
    expect(screen.getByText(/serviceAccountRole — Skipped/i)).toBeInTheDocument();
    expect(screen.getByText(/import \(write\): ALL/i)).toBeInTheDocument();
  });

  /*
   * S1 replaces the whole window list on a patch, so an edit that touched the timeout must not
   * send a `protection` block at all — that is the difference between an amendment and a wipe.
   */
  it('does not resend commit windows an edit never touched', async () => {
    const existing = db.state.environments.find((e) => e.environmentKey === 'env-prod');
    let body: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/v1/environments/env-prod', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(existing);
      }),
    );
    renderForm({ environment: existing ?? null, onClose: vi.fn() });

    // the stored schedule comes back from the protection row, prefilled and left alone
    await waitFor(() => expect(screen.getByLabelText(/^Timezone/)).toHaveValue('Africa/Addis_Ababa'));
    const timeoutField = screen.getByLabelText(/^Timeout/);
    await userEvent.clear(timeoutField);
    await userEvent.type(timeoutField, '30');
    await userEvent.click(save());

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({ timeoutSeconds: 30 });
  });

  /*
   * The two operations that were API-only: probing writes a capability profile the deployment
   * stages then read, and disabling takes the environment out of service. Both are gated by
   * S1's per-target answer, so neither is offered for an environment already out of service.
   */
  it('probes capabilities and disables an environment through S1', async () => {
    const probed: string[] = [];
    const disabled: string[] = [];
    server.use(
      http.post('/api/v1/environments/:key/probe-capabilities', ({ params }) => {
        probed.push(String(params.key));
        return HttpResponse.json({ capabilityProfileArtifactId: 'art-cap', version: '2.41.4', profileVersion: '1' });
      }),
      http.post('/api/v1/environments/:key/disable', ({ params }) => {
        disabled.push(String(params.key));
        return HttpResponse.json({ environmentKey: String(params.key), status: 'DISABLED' });
      }),
    );
    renderPage();

    await userEvent.click(await enabledButton(/Probe capabilities/, 0));
    await waitFor(() => expect(probed).toEqual(['env-dev']));
    expect(await screen.findByText(/env-dev: 2\.41\.4/)).toBeInTheDocument();

    await userEvent.click(await enabledButton(/^Disable$/, 0));
    await userEvent.click(await enabledButton(/^Confirm$/));
    await waitFor(() => expect(disabled).toEqual(['env-dev']));
  });

  /*
   * `env-stg` is the fixture's out-of-service row. S1 answers every environment action for it and
   * refuses the three that speak to the instance, while the two that could bring it back stay
   * open -- the console has to follow that split rather than grey the row out or offer all five.
   */
  it('refuses the instance operations on a disabled environment and keeps the row editable', async () => {
    renderPage();
    const row = (await screen.findByText('https://staging.dhis2.org/api')).closest('tr');
    if (!row) throw new Error('no row for env-stg');

    await waitFor(() => expect(within(row).getByRole('button', { name: /^Disable$/ })).toBeDisabled());
    expect(within(row).getByRole('button', { name: /Test connection/ })).toBeDisabled();
    expect(within(row).getByRole('button', { name: /Probe capabilities/ })).toBeDisabled();
    expect(within(row).getByRole('button', { name: /^Edit$/ })).toBeEnabled();
    expect(within(row).getByRole('button', { name: /Emergency lock|Release lock/ })).toBeEnabled();
  });

  it('will not lock an environment without a reason, and sends one when it does', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/environments/:key/emergency-lock', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ environmentKey: 'env-dev', emergencyLocked: true });
      }),
    );
    renderPage();
    await userEvent.click(await enabledButton(/Emergency lock/));

    const confirm = (await screen.findByRole('button', { name: /^Confirm$/ })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(captured.body).toBeNull();

    await userEvent.type(screen.getByLabelText(/Reason/), 'reconciling a production import');
    await userEvent.click(await enabledButton(/^Confirm$/));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({ locked: true, reason: 'reconciling a production import' });
  });

  /*
   * The row whose `details.locked` S1 answered with is the only reason this page knows the
   * environment is locked at all — `status` is ACTIVE or DISABLED, and the release is a second
   * click, not a form that demands another reason for doing the safe thing.
   */
  it('offers a release for the environment S1 says is locked, and takes no reason for it', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/environments/:key/emergency-lock', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ environmentKey: 'env-prod', emergencyLocked: false });
      }),
    );
    renderPage();

    await screen.findAllByText('Production');
    const labels = () => screen.getAllByRole('button').map((b) => b.textContent);
    expect(labels()).toContain('Release lock');
    const release = await enabledButton(/^Release lock$/);
    await userEvent.click(release);

    const confirm = (await screen.findByRole('button', { name: /^Confirm$/ })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(false);
    await userEvent.click(confirm);

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({ locked: false });
    expect(captured.body).not.toHaveProperty('reason');
  });
});

/*
 * A fresh install lands here with nothing registered. An empty DataTable with no sentence reads as
 * a broken page rather than as "configure me first", which is the failure UI-SET-003 exists to stop.
 */
describe('the empty environment list (UI-SET-003)', () => {
  it('says what is missing and points at the checklist', async () => {
    server.use(
      http.get('/api/v1/environments', () => HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 })),
    );
    cleanup();
    useOperatorStore.getState().setName('Ana');
    render(
      <AppProviders>
        <MemoryRouter initialEntries={['/environments']}>
          <AppRoutes />
        </MemoryRouter>
      </AppProviders>,
    );
    expect(await screen.findByText(/No DHIS2 environment is registered yet/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /See what else is still missing/i })).toHaveAttribute('href', '/getting-started');
    // the create control is still S1's to allow, not the page's to decide
    expect(((await screen.findByRole('button', { name: /Add environment/i })) as HTMLButtonElement).disabled).toBe(false);
  });

  it('tells the operator to test the connection after saving', async () => {
    server.use(
      http.post('/api/v1/environments', () => HttpResponse.json({ environmentKey: 'env-new' }, { status: 201 })),
    );
    cleanup();
    useOperatorStore.getState().setName('Ana');
    render(
      <AppProviders>
        <MemoryRouter initialEntries={['/environments']}>
          <AppRoutes />
        </MemoryRouter>
      </AppProviders>,
    );
    // the create control is gated by the platform answer, so it has to arrive enabled before the click
    const createButton = () => screen.getByRole('button', { name: /Add environment/i }) as HTMLButtonElement;
    await waitFor(() => expect(createButton().disabled).toBe(false));
    await userEvent.click(createButton());
    await userEvent.type(screen.getByLabelText(/^Environment key/), 'env-new');
    await userEvent.type(screen.getByLabelText(/^Base URL/), 'https://env-new.invalid');
    await userEvent.type(screen.getByLabelText(/^Version constraint/), '2.41.x');
    await userEvent.type(nthLabel(/^Secret reference/, 0), 'env://NEW_DISCOVERY');
    await userEvent.type(nthLabel(/^Secret reference/, 1), 'env://NEW_IMPORT');
    await waitFor(() => expect(save().disabled).toBe(false));
    await userEvent.click(save());

    expect(await screen.findByText(/Run a connection test before the first validation stage/i)).toBeInTheDocument();
  }, 15000);
});
