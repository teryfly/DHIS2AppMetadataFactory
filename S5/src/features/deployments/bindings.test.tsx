/*
 * UI-DEP-003 (BP-REG-010): a binding the compiler could not resolve is set by hand here, from
 * the candidates in the stored discovery result, and the note that says why is part of the
 * request — S2 refuses the binding without one, so the form asks before S1 has to answer.
 * UI-DEP-003 / FR-UI-009 also fixes where availability comes from: the button is clickable only
 * while S1 lists registry_binding.set, never because the row looks unresolved.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

const SEED = () => [
  {
    businessKey: 'dataElement.weight',
    uid: 'UomZjGhT5bc',
    resolution: 'AUTO',
    resolvedBy: null,
    note: 'matched by code',
    pendingReparse: false,
    environmentKey: 'env-dev',
    blueprintVersion: 'v7',
    observedLastUpdated: null,
    observedHash: null,
    discoveryResultId: 'DISC-1',
  },
  {
    businessKey: 'categoryOptionGroupSet.ageBand',
    uid: null,
    resolution: null,
    resolvedBy: null,
    note: null,
    pendingReparse: false,
    environmentKey: 'env-dev',
    blueprintVersion: 'v7',
    observedLastUpdated: null,
    observedHash: null,
    discoveryResultId: 'DISC-1',
  },
];

/** the panel announces itself as a named region, which is also the scope these tests read */
async function panelRegion() {
  return await screen.findByRole('region', { name: /Environment bindings/ });
}

function renderConsole() {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[`/projects/${db.DESIGN_PROJECT_ID}/deployments/RUN-1`]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
  // the mock's PUT writes onto the fixture, so each test starts from the unresolved row again
  db.state.bindings.length = 0;
  db.state.bindings.push(...SEED());
});
afterEach(() => server.resetHandlers());

describe('Hand-setting an environment binding (UI-DEP-003)', () => {
  it('binds the chosen candidate only with a note, and shows it as MANUAL afterwards', async () => {
    const seen: unknown[] = [];
    server.use(
      http.put(`/api/v1/deployment-runs/:id/environment-bindings/:businessKey`, async ({ request }) => {
        seen.push(await request.json());
        const row = db.state.bindings.find((b) => b.businessKey === 'categoryOptionGroupSet.ageBand');
        if (!row) throw new Error('fixture lost');
        row.uid = 'YrB8xKN9yOl';
        row.resolution = 'MANUAL';
        row.note = 'the register only ever has one age band set';
        row.resolvedBy = request.headers.get('X-Operator');
        return HttpResponse.json({ ...row });
      }),
    );

    renderConsole();
    const panel = await panelRegion();

    // unresolved row offers the hand-binding control; the AUTO row does not
    await userEvent.click(within(panel).getByRole('button', { name: /Set binding/ }));

    const group = await screen.findByRole('radio', { name: /YrB8xKN9yOl/ });
    await userEvent.click(group);

    // submitting without the note is stopped here rather than answered by S1's 422
    await userEvent.click(within(panel).getAllByRole('button', { name: /Set binding/ }).at(-1)!);
    expect(await screen.findByText(/A note is required/)).toBeInTheDocument();
    expect(seen).toHaveLength(0);

    await userEvent.type(screen.getByLabelText(/Note/), 'the register only ever has one age band set');
    await userEvent.click(within(panel).getAllByRole('button', { name: /Set binding/ }).at(-1)!);

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0]).toMatchObject({ uid: 'YrB8xKN9yOl' });
    expect(await within(panel).findByText('MANUAL')).toBeInTheDocument();
    // the picker closes once the binding resolved
    expect(screen.queryByRole('radio', { name: /YrB8xKN9yOl/ })).not.toBeInTheDocument();
  }, 15000);

  it('asks the server to narrow the candidates instead of shipping the whole discovery result', async () => {
    let asked: string | null = null;
    server.use(
      http.get(`/api/v1/deployment-runs/:id/environment-bindings/:businessKey/candidates`, ({ request }) => {
        asked = new URL(request.url).searchParams.get('query');
        const hit = db.state.candidateObjects.filter((c) =>
          asked ? `${c.code} ${c.name}`.toLowerCase().includes(String(asked).toLowerCase()) : true,
        );
        return HttpResponse.json({ items: hit, total: hit.length, businessKey: 'categoryOptionGroupSet.ageBand' });
      }),
    );

    renderConsole();
    const panel = await panelRegion();
    await userEvent.click(within(panel).getByRole('button', { name: /Set binding/ }));
    await screen.findByRole('radio', { name: /AGE_BAND_0_5/ });

    await userEvent.type(screen.getByLabelText(/Search code or name/), '50 and above');
    await waitFor(() => expect(asked).toBe('50 and above'));
    // the list follows the query rather than keeping all four on screen
    await waitFor(() => expect(screen.getAllByRole('radio')).toHaveLength(1));
  });

  it('closes the control when S1 does not list registry_binding.set (FR-UI-009)', async () => {
    const allowed = {
      ...db.allowedActions1,
      allowed: (db.allowedActions1.allowed ?? []).filter((a) => a.action !== 'registry_binding.set'),
      unavailable: [
        ...(db.allowedActions1.unavailable ?? []),
        { action: 'registry_binding.set', available: false, reasonCode: 'NO_OPEN_RUN' },
      ],
    };
    server.use(http.get(`/api/v1/workflows/:id/allowed-actions`, () => HttpResponse.json(allowed)));

    renderConsole();
    const panel = await panelRegion();
    const buttons = await within(panel).findAllByRole('button', { name: /Set binding/ });
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) expect(button).toBeDisabled();
  });
});
