/*
 * UI-AUTH-001 / FR-SEC-001: an operator changes their own password from the shell.
 *
 * The console could already write a password — but only an administrator writing somebody else's,
 * on the Users page. That left the account S1 flags `mustChangePassword` with nowhere to go, and it
 * is the reason this test mounts the real chrome (TopBar + dialog) rather than the component: the
 * entry point, the dialog, and the session read it depends on are three different files, and the
 * bug class here is exactly "the pieces exist but nothing joins them".
 *
 * The property that decides the shape of the code is that changing the digest revokes the account's
 * live sessions (`identity/service.py` → `sessions.revoke_for_user`), so a successful change must
 * leave the app asking S1 who is here and getting "nobody". The assertions below follow that, and
 * one of them is simply that the typed value never appears in the document.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '@/mocks/server';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useCsrfStore } from '@/stores/csrf';

const SENTINEL = 'N3w-K3ntosh-Value';
const P = '/api/v1';

let signedIn = true;
let mustChange = false;

function session() {
  return {
    authenticated: signedIn,
    authMode: 'local',
    userId: signedIn ? 'usr-ada' : null,
    subject: signedIn ? 'ada@example.org' : null,
    displayName: signedIn ? 'Ada' : null,
    roles: signedIn ? ['PLATFORM_ADMINISTRATOR'] : [],
    csrfToken: signedIn ? 'mock-csrf-1' : null,
    expiresAt: signedIn ? new Date(Date.now() + 1800_000).toISOString() : null,
    mustChangePassword: mustChange,
  };
}

/** The top bar's entry and the dialog's submit are named differently on purpose: one document, two
 *  buttons, and a query that cannot silently mean either. */
const entry = () => screen.findByRole('button', { name: 'Change password' });
const submit = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: 'Save new password' });

function openDialog() {
  // the same provider tree `app.integration.test.tsx` mounts: the chrome (TopBar, SidebarNav) only
  // exists inside a router, and the entry point this file is about lives in that chrome
  render(
    <AppProviders>
      <MemoryRouter initialEntries={['/']}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  signedIn = true;
  mustChange = false;
  useOperatorStore.getState().setName('Ada');
  useCsrfStore.getState().setToken('mock-csrf-1');
  useCsrfStore.getState().clearLapsed();
  server.use(
    http.get(`${P}/auth/session`, () => HttpResponse.json(session())),
    // every read the shell makes, answered empty: this file is about the session, not the content
    http.get(`${P}/notifications`, () => HttpResponse.json({ items: [], total: 0 })),
  );
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

describe('change my own password (UI-AUTH-001)', () => {
  it('reaches the write with the shortest path the operator has', async () => {
    const written: { url: string; body: Record<string, unknown> }[] = [];
    server.use(
      http.post(`${P}/users/:userId/password`, async ({ request }) => {
        written.push({ url: new URL(request.url).pathname, body: (await request.json()) as Record<string, unknown> });
        signedIn = false; // S1 revoked every session of this account, this one included
        return HttpResponse.json({ cleared: true });
      }),
    );
    openDialog();

    await userEvent.click(await screen.findByRole('button', { name: 'Change password' }));

    const dialog = await screen.findByRole('dialog');
    const tooShort = within(dialog).getByLabelText(/^New password/);
    await userEvent.type(tooShort, 'short');
    // the floor S1 enforces in the service is stated here too, so the first time an operator meets it
    // is not a 422 they have to interpret
    expect(within(dialog).getByRole('button', { name: 'Save new password' })).toBeDisabled();
    expect(tooShort).toHaveValue('short');

    await userEvent.clear(tooShort);
    await userEvent.type(tooShort, SENTINEL);
    await userEvent.type(within(dialog).getByLabelText(/Repeat the new password/), 'a-different-value');
    expect(within(dialog).getByText(/do not match/i)).toBeInTheDocument();

    const confirm = within(dialog).getByLabelText(/Repeat the new password/);
    await userEvent.clear(confirm);
    await userEvent.type(confirm, SENTINEL);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Save new password' }));

    // the write names the person asking, never somebody else
    await waitFor(() => expect(written).toHaveLength(1));
    const [first] = written;
    expect(first?.url).toBe(`${P}/users/usr-ada/password`);
    expect(first?.body).toEqual({ password: SENTINEL, mustChangePassword: false });
  });

  it('ends the session instead of leaving the app claiming an identity S1 no longer knows', async () => {
    server.use(
      http.post(`${P}/users/:userId/password`, () => {
        signedIn = false;
        return HttpResponse.json({ cleared: true });
      }),
    );
    openDialog();
    await userEvent.click(await entry());

    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText(/^New password/), SENTINEL);
    await userEvent.type(within(dialog).getByLabelText(/Repeat the new password/), SENTINEL);
    await userEvent.click(submit(dialog));

    // the CSRF value dies with the session; keeping it would let a later write look allowed
    await waitFor(() => expect(useCsrfStore.getState().token).toBeNull(), { timeout: 2500 });
    // and the app marks itself lapsed — the signal SessionGate uses to take the operator back to
    // sign-in with an explanation, rather than leaving a shell that still displays their name
    expect(useCsrfStore.getState().lapsed).toBe(true);
    expect(document.body.textContent).not.toContain(SENTINEL);
  }, 20_000);

  it('offers nothing while nobody is signed in', async () => {
    signedIn = false;
    openDialog();
    // the name prompt is the off-mode affordance; a session-less shell has no identity to change
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole('button', { name: 'Change password' })).not.toBeInTheDocument();
  });

  it('explains a flagged account in words, not only as a mark on a button', async () => {
    mustChange = true;
    openDialog();
    // CP-IAM-005 ③ keeps the flag report-only, so nothing will stop this operator — which is exactly
    // why the console has to say what the ⚠ means instead of leaving it to be guessed at.
    // Found by its sentence, not by `role="status"`: the page container is a live region while it
    // loads, so the role alone would match something that has nothing to do with the password.
    const line = await screen.findByText(/must change its password/i);
    const notice = line.closest('div') as HTMLElement;
    expect(notice).toBeInTheDocument();
    await userEvent.click(within(notice).getByRole('button', { name: 'Change password' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('says nothing about a password that does not need changing', async () => {
    openDialog();
    await screen.findByRole('button', { name: 'Change password' });
    expect(screen.queryByText(/must change its password/i)).not.toBeInTheDocument();
  });
});
