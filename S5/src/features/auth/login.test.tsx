/*
 * UI-AUTH-001/002: signing in, and what a deployment without accounts must keep doing.
 *
 * The second half matters as much as the first: CP-IAM-001 says M4's switch is inert while it is
 * off, so an app that suddenly asks for a session under `authMode: off` would break every M1/M2
 * screen and the tests written against them.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useCsrfStore } from '@/stores/csrf';
import * as db from '@/mocks/db';
import { http, HttpResponse } from 'msw';
import { server } from '@/mocks/server';

function renderAt(path = '/') {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe('the sign-in screen (UI-AUTH-001)', () => {
  beforeEach(() => {
    db.auth.mode = 'local';
    db.auth.signOut();
    useCsrfStore.getState().setToken(null);
    useOperatorStore.getState().clear();
  });

  afterEach(() => {
    db.auth.mode = 'off';
    db.auth.signOut();
    useCsrfStore.getState().setToken(null);
    cleanup();
  });

  it('routes an unauthenticated visit to sign-in without a toast per request', async () => {
    renderAt('/projects');
    expect(await screen.findByLabelText(/Password/, {}, { timeout: 9000 })).toBeInTheDocument();
    // every read of that page failed with the same 401; UI-AUTH-002 allows one notice, not a stack
    expect(screen.queryAllByRole('alert')).toHaveLength(0);
    // nothing has lapsed here — this caller never signed in — so the page must not claim otherwise
    expect(screen.queryByText(/session has expired|会话已失效/)).not.toBeInTheDocument();
  });

  it('refuses wrong credentials, says why, and does not leave the typed password behind', async () => {
    renderAt('/projects');
    await userEvent.type(await screen.findByLabelText(/Account/, {}, { timeout: 9000 }), 'ada@example.org');
    await userEvent.type(screen.getByLabelText(/Password/), 'not-the-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    // The console's own words for the code, not the server's sentence: `translateErrorCode` prefers
    // the catalogue and falls back to the message, which is what makes this readable in Chinese too.
    expect(await screen.findByText(/Wrong account name or password/)).toBeInTheDocument();
    // The failure notice also has to say what repeated failures cost, because the refusal itself
    // cannot — S1 answers a lock and a wrong password identically (CP-IAM-005 ④).
    expect(screen.getByText(/freeze this account for 15 minutes/i)).toBeInTheDocument();
    expect(db.auth.session.authenticated).toBe(false);
    // and the secret they just typed is not left on screen while they read the reason
    expect(screen.getByLabelText(/Password/)).toHaveValue('');
  });

  it('explains why the sign-in button is dead instead of just disabling it', async () => {
    renderAt('/projects');
    await screen.findByLabelText(/Account/, {}, { timeout: 9000 });
    // An unexplained disabled control reads to the operator as a broken one, and from here the two
    // are indistinguishable.
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
    expect(screen.getByText(/Fill in both the account and the password/i)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/Account/), 'ada@example.org');
    expect(screen.getByText(/Fill in both the account and the password/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Password/), 'correct-horse');
    expect(screen.queryByText(/Fill in both the account and the password/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled();
  });

  it('signs in, names the operator after the session, and hands the CSRF value to the store', async () => {
    renderAt('/projects');
    await userEvent.type(await screen.findByLabelText(/Account/, {}, { timeout: 9000 }), 'ada@example.org');
    await userEvent.type(screen.getByLabelText(/Password/), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(screen.queryByLabelText('Password')).not.toBeInTheDocument());
    expect(db.auth.session.authenticated).toBe(true);
    // CP-IAM-003: the session subject is the identity, so the client aligns its declared name with it
    expect(useOperatorStore.getState().name).toBe('ada@example.org');
    expect(useCsrfStore.getState().token).toBeTruthy();
    expect(await screen.findByRole('button', { name: /Sign out/ })).toBeInTheDocument();
  });

  it('notices exactly once when the session lapses, and not once per request (UI-AUTH-002)', async () => {
    renderAt('/projects');
    await userEvent.type(await screen.findByLabelText(/Account/, {}, { timeout: 9000 }), 'ada@example.org');
    await userEvent.type(screen.getByLabelText(/Password/), 'correct-horse');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(screen.queryByLabelText(/Password/)).not.toBeInTheDocument());

    // the idle clock runs out with a page open. The next read that fails is the only signal there
    // is: it must re-ask who this is once, and land on sign-in — not announce itself per request.
    db.auth.signOut();
    server.use(
      http.get('/api/v1/governance/models', () =>
        HttpResponse.json(
          { errorCode: 'UNAUTHENTICATED', message: 'the session has expired', retryable: false, correlationId: 'corr-expired', details: {} },
          { status: 401 },
        ),
      ),
    );
    await userEvent.click(screen.getByRole('link', { name: /^Governance$/ }));

    expect(await screen.findByLabelText(/Account/, {}, { timeout: 9000 })).toBeInTheDocument();
    // exactly one notice, and it is a status line rather than a stack of alerts
    expect(await screen.findByText(/session has expired|会话已失效/)).toBeInTheDocument();
    expect(screen.queryAllByRole('alert')).toHaveLength(0);
  });

  it('says so when the single sign-on response carries no code', async () => {
    renderAt('/auth/callback');
    expect(await screen.findByText(/carried no authorization code|未携带授权码/)).toBeInTheDocument();
  });

  it('stays out of the way entirely when the deployment has no accounts (CP-IAM-001)', async () => {
    db.auth.mode = 'off';
    renderAt('/projects');
    expect(screen.queryByLabelText(/Password/)).not.toBeInTheDocument();
    // the M1/M2 name prompt is still what asks for an identity, exactly as before M4
    expect(await screen.findByText(/enter your operator name/i)).toBeInTheDocument();
  });
});
