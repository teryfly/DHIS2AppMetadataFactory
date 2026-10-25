/*
 * UI-ADM-005 (AI-COST-001~003, S5IF-GOV18): the usage rollup and the budget switch.
 *
 * The two properties worth pinning are that the state shown is the state S1 answered — the screen
 * must not compare `spent` to `limit` and arrive at its own verdict — and that a cookie-authenticated
 * write names the CSRF value, since that is what makes the write possible at all (FR-SEC-005).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { CostPanel } from './CostPanel';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';
import { useOperatorStore } from '@/stores/operator';
import { useCsrfStore } from '@/stores/csrf';

const P = '/api/v1';

function renderPanel() {
  return render(
    <AppProviders>
      <MemoryRouter>
        <CostPanel />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe('the cost panel (UI-ADM-005)', () => {
  beforeEach(() => {
    useOperatorStore.getState().setName('ada');
    useCsrfStore.getState().setToken('csrf-from-session');
    delete db.budgets[db.DESIGN_PROJECT_ID];
  });

  afterEach(() => {
    db.budgets[db.DESIGN_PROJECT_ID] = {
      projectId: db.DESIGN_PROJECT_ID,
      state: 'WARNING',
      spent: 4.2,
      limit: 5,
      currency: 'USD',
      thresholdReached: 0.8,
    };
    useCsrfStore.getState().setToken(null);
    cleanup();
  });

  it('rolls usage up by the dimensions S1 declares', async () => {
    renderPanel();
    expect(await screen.findByRole('columnheader', { name: 'Stage' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Model' })).toBeInTheDocument();
    expect(screen.getAllByRole('row').length).toBeGreaterThan(2);
  });

  it('writes the budget with the CSRF value the session carried', async () => {
    let headers: Headers | null = null;
    server.use(
      http.put(`${P}/governance/budget`, ({ request }) => {
        headers = request.headers;
        return HttpResponse.json({
          projectId: db.DESIGN_PROJECT_ID,
          state: 'OK',
          spent: 4.2,
          limit: 100,
          currency: 'USD',
          thresholdReached: null,
        });
      }),
    );
    renderPanel();
    const limit = await screen.findByLabelText('Limit');
    await userEvent.type(limit, '100');
    await userEvent.click(screen.getByRole('button', { name: 'Save budget' }));

    await waitFor(() => expect(headers).not.toBeNull());
    expect((headers as unknown as Headers).get('X-CSRF-Token')).toBe('csrf-from-session');
    expect((headers as unknown as Headers).get('X-Operator')).toBe('ada');
  });
});
