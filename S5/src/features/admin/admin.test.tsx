/*
 * UI-AUTH-003: the Administration screens, and the one rule they all share — a control is enabled
 * only because S1's allowed-actions said so, and the reason a disabled control shows is S1's reason.
 *
 * The exemption case is deliberate: the mock marks `production_exemption.manage` unavailable for
 * this project while the other rights are available, which is exactly the situation a screen must
 * render honestly rather than smooth over. The three project screens ask the *workflow* answer for
 * them — S1 filters those rights by project id (CP-IAM-002), so a role granted for one project
 * alone can manage that project without holding the right everywhere.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { UsersView } from './UsersView';
import { RolesView } from './RolesView';
import { ProjectMembers } from '../project/ProjectMembers';
import { ArtifactGrants } from '../project/ArtifactGrants';
import { ProductionExemptions } from '../project/ProductionExemptions';
import { server } from '@/mocks/server';
import { useOperatorStore } from '@/stores/operator';
import * as db from '@/mocks/db';

const PROJECT = 'PRJ-green-01';

function renderPlatform(element: React.ReactElement) {
  return render(<AppProviders><MemoryRouter>{element}</MemoryRouter></AppProviders>);
}

/** Project screens read `:projectId` from the route, so they need one matching path. */
function renderProject(element: React.ReactElement, tab: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[`/projects/${PROJECT}/${tab}`]}>
        <Routes>
          <Route path="/projects/:projectId/:tab" element={element} />
        </Routes>
      </MemoryRouter>
    </AppProviders>,
  );
}

describe('the administration screens (UI-AUTH-003)', () => {
  beforeEach(() => useOperatorStore.getState().setName('ada'));
  afterEach(cleanup);

  it('lists the accounts it was given', async () => {
    renderPlatform(<UsersView />);
    // the same subject appears in the table and in the add-member select, so count rather than pick one
    expect((await screen.findAllByText('ada@example.org')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('cyrus@example.org').length).toBeGreaterThan(0);
  });

  it('creates an account under two names: the one it signs in with and the one it is read by', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/users', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'USR-new', subject: captured.body?.subject, source: 'local', status: 'ACTIVE' }, { status: 201 });
      }),
    );
    renderPlatform(<UsersView />);
    await screen.findAllByText('ada@example.org');
    await userEvent.type(screen.getByLabelText(/^New account/), 'new@example.org');
    await userEvent.type(screen.getByLabelText('Display name'), 'Newcomer Wei');
    const password = screen.getByLabelText(/^Initial password/) as HTMLInputElement;
    await userEvent.type(password, 'a-first-secret');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body?.subject).toBe('new@example.org');
    // the login name and the display name are separate fields in S1's `UserCreateRequest`, and an
    // operator who types a name expects it to arrive — not to be silently dropped
    expect(captured.body?.displayName).toBe('Newcomer Wei');
    expect((password as HTMLInputElement).value).toBe('');
    expect(document.body.textContent).not.toContain('a-first-secret');
  });

  it('says an account is locked instead of leaving ACTIVE to be explained', async () => {
    // five bad attempts freeze an account for fifteen minutes and the sign-in answer deliberately
    // says nothing about it (UI-AUTH-003's non-enumeration rule), so this table is where the fact
    // surfaces — and `status` keeps reporting ACTIVE, which is exactly why the tag is not enough
    const locked = db.users.find((u) => u.subject === 'ada@example.org');
    expect(locked).toBeDefined();
    if (!locked) return;
    locked.locked = true;
    try {
      renderPlatform(<UsersView />);
      await screen.findAllByText('ada@example.org');
      // located as a row, not by `closest('tr')` on a text match: the subject appears in the table
      // *and* in the add-member select, and an ambient DOM walk from whichever one it picked is
      // exactly the fragile half of this assertion
      const row = screen.getAllByRole('row').find((r) => r.textContent?.includes('ada@example.org'));
      expect(row).toBeDefined();
      if (!row) return;
      expect(within(row).getByText('locked')).toBeInTheDocument();
      expect(within(row).getByText('Enabled')).toBeInTheDocument();
    } finally {
      locked.locked = false;
    }
  });

  it('retires the account it was pressed on, by its id', async () => {
    // S1 has no delete, so this button *is* how an account leaves the system. It is the one write on
    // this screen that no test had pressed, and the mutation is built with the user id bound into
    // its path — which is exactly the shape that goes out empty if the row is forgotten.
    const asked: { url: string; userId: string; body: Record<string, unknown> }[] = [];
    server.use(
      http.patch('/api/v1/users/:userId', async ({ request, params }) => {
        asked.push({
          url: new URL(request.url).pathname,
          userId: String(params.userId),
          body: (await request.json()) as Record<string, unknown>,
        });
        return HttpResponse.json({ id: String(params.userId), status: 'DISABLED' });
      }),
    );
    renderPlatform(<UsersView />);
    const button = await screen.findByRole('button', { name: 'Disable' });
    await userEvent.click(button);

    await waitFor(() => expect(asked).toHaveLength(1));
    const [retire] = asked;
    expect(retire?.userId).toBe('USR-ada');
    expect(retire?.url).toBe('/api/v1/users/USR-ada');
    expect(retire?.body).toEqual({ status: 'DISABLED' });
  });

  it('corrects the display name through the endpoint that owns it, and leaves the login name alone', async () => {
    const asked: { url: string; body: Record<string, unknown> }[] = [];
    server.use(
      http.patch('/api/v1/users/:userId', async ({ request, params }) => {
        asked.push({ url: new URL(request.url).pathname, body: (await request.json()) as Record<string, unknown> });
        return HttpResponse.json({ id: String(params.userId), displayName: 'Ada L.' });
      }),
    );
    renderPlatform(<UsersView />);
    await screen.findAllByText('ada@example.org');
    // every row has its own Edit button, so the click is aimed at one account rather than at "the
    // first Edit on the page" — which would be a test that passes only while the mock has an order
    const rows = screen.getAllByRole('row');
    const adaRow = rows.find((r) => r.textContent?.includes('ada@example.org')) as HTMLElement;
    expect(adaRow).toBeDefined();
    await userEvent.click(within(adaRow).getByRole('button', { name: 'Edit' }));

    // scoped to the edit card: the create card below has a field with the same label, and an
    // unscoped query would match whichever one it liked
    const heading = await screen.findByText('Edit ada@example.org');
    const card = heading.parentElement as HTMLElement;
    const name = within(card).getByLabelText('Display name');
    // prefilled from the row: an administrator correcting a name should not have to remember it
    expect(name).toHaveValue('ada');
    await userEvent.clear(name);
    await userEvent.type(name, 'Ada L.');
    await userEvent.click(within(card).getByRole('button', { name: 'Save account' }));

    await waitFor(() => expect(asked).toHaveLength(1));
    const [corrected] = asked;
    expect(corrected?.url).toBe('/api/v1/users/USR-ada');
    expect(corrected?.body).toEqual({ displayName: 'Ada L.', email: 'ada@example.org' });
    // the login name is not in the body: it is the string the audit trail carries
    expect(corrected?.body.subject).toBeUndefined();
  });

  it('lets the operator decide whether a new account must change its password', async () => {
    const sent: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/v1/users', async ({ request }) => {
        sent.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: 'USR-x', subject: 'x@example.org', status: 'ACTIVE' }, { status: 201 });
      }),
    );
    renderPlatform(<UsersView />);
    await screen.findAllByText('ada@example.org');
    await userEvent.type(screen.getByLabelText(/^New account/), 'x@example.org');
    await userEvent.type(screen.getByLabelText(/^Initial password/), 'a-first-secret');

    const flag = screen.getByLabelText(/Must change/i);
    expect(flag).toBeChecked();
    await userEvent.click(flag);
    expect(flag).not.toBeChecked();
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));

    await waitFor(() => expect(sent).toHaveLength(1));
    const [created] = sent;
    expect(created?.mustChangePassword).toBe(false);
  });

  it('grants two roles as two actions, and can scope one of them to a project', async () => {
    // The key is derived from the intent, and an intent that names only the *account* makes every
    // role for that account the same logical action: S1 would answer the second grant with the first
    // one's stored replay (CP-API-003) and the role would silently never land.
    const sent: { body: Record<string, unknown>; key: string | null }[] = [];
    server.use(
      http.put('/api/v1/users/:userId/roles', async ({ request, params }) => {
        sent.push({
          body: (await request.json()) as Record<string, unknown>,
          key: request.headers.get('Idempotency-Key'),
        });
        return HttpResponse.json({ userId: String(params.userId), role: 'X', revoked: false });
      }),
    );
    renderPlatform(<UsersView />);
    // the subject is in the table and in the grant card's account select, so wait on a count
    await screen.findAllByText('ada@example.org');
    await userEvent.selectOptions(screen.getByLabelText('Account'), 'USR-ada');
    await userEvent.selectOptions(screen.getByLabelText(/^Role$/), 'SOLUTION_ARCHITECT');
    await userEvent.click(screen.getByRole('button', { name: 'Grant role' }));
    await waitFor(() => expect(sent).toHaveLength(1));

    await userEvent.selectOptions(screen.getByLabelText(/^Role$/), 'DEPLOYMENT_APPROVER');
    await userEvent.selectOptions(screen.getByLabelText('Project scope'), 'PRJ-green-01');
    await userEvent.click(screen.getByRole('button', { name: 'Grant role' }));
    await waitFor(() => expect(sent).toHaveLength(2));

    const [firstGrant, secondGrant] = sent;
    expect(firstGrant?.body).toEqual({ role: 'SOLUTION_ARCHITECT' });
    expect(secondGrant?.body).toEqual({ role: 'DEPLOYMENT_APPROVER', projectId: 'PRJ-green-01' });
    expect(firstGrant?.key).not.toBeNull();
    expect(secondGrant?.key).not.toBe(firstGrant?.key);
    // the field is optional: leaving it empty must not send an empty scope
    expect(firstGrant?.body.projectId).toBeUndefined();
  });

  it('revokes the role an account actually holds, named in the request', async () => {
    const asked: { userId: string; role: string | null; projectId: string | null }[] = [];
    server.use(
      http.delete('/api/v1/users/:userId/roles', ({ request, params }) => {
        const sp = new URL(request.url).searchParams;
        asked.push({ userId: String(params.userId), role: sp.get('role'), projectId: sp.get('projectId') });
        // the shared mock clears the assignment from the row it just revoked, which is what S1's
        // delete does; this override has to keep that or the re-read below shows a stale table
        const row = db.users.find((u) => u.id === params.userId);
        if (row) row.roles = [];
        return HttpResponse.json({ userId: String(params.userId), role: sp.get('role') ?? '', projectId: null, revoked: true });
      }),
    );
    renderPlatform(<UsersView />);
    // the button belongs to a held assignment; the catalogue in the grant dropdown is not a source
    // for what to take away
    const revoke = await screen.findByRole('button', { name: 'Revoke' });
    await userEvent.click(revoke);

    await waitFor(() => expect(asked).toEqual([{ userId: 'USR-ada', role: 'SOLUTION_ARCHITECT', projectId: db.DESIGN_PROJECT_ID }]));
    // the row loses the assignment on the re-read, and the control goes with it
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument());
  });

  it('lists the built-in roles with what each one grants', async () => {
    renderPlatform(<RolesView />);
    expect(await screen.findByText('Solution architect')).toBeInTheDocument();
    expect(screen.getAllByText('gate.approve').length).toBeGreaterThan(0);
  });

  it('replaces the whole member set in one request when one person is added', async () => {
    const memberPut: { body: { members?: Record<string, unknown>[] } | null } = { body: null };
    server.use(
      http.put('/api/v1/projects/:id/members', async ({ request }) => {
        memberPut.body = (await request.json()) as { members?: Record<string, unknown>[] };
        // the mock does not persist a replaced set, so the assertion below is about the request
        return HttpResponse.json({ items: memberPut.body?.members ?? [], total: (memberPut.body?.members ?? []).length });
      }),
    );
    renderProject(<ProjectMembers />, 'members');
    await screen.findAllByText('ada@example.org');
    await userEvent.click(screen.getByRole('button', { name: 'Add member' }));
    expect(screen.getByRole('button', { name: 'Add member' })).toBeDisabled();

    // pick a person and a role, then the set is replaced in one intent
    const selects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(selects[0] as HTMLElement, 'cyrus@example.org');
    await userEvent.selectOptions(selects[1] as HTMLElement, 'SOLUTION_ARCHITECT');
    await userEvent.click(screen.getByRole('button', { name: 'Add member' }));

    await waitFor(() => expect(memberPut.body).not.toBeNull());
    const written = memberPut.body?.members ?? [];
    expect(written.length).toBe(2);
    expect(written.map((row) => row.userId)).toContain('USR-cyrus');
  });

  it('lists an authorisation with its reason, and withholds a write with no reason (FR-SEC-003)', async () => {
    renderProject(<ArtifactGrants />, 'grants');
    // FR-SEC-003: a restricted read is granted for a stated reason, and the reason is on screen
    expect((await screen.findAllByText('ward list review')).length).toBe(1);
    expect(screen.getAllByText(/health data/).length).toBeGreaterThan(0);
    // the new authorisation cannot be submitted until it carries a reason
    expect(screen.getByRole('button', { name: 'Authorise' })).toBeDisabled();
  });

  it('shows no way to act when S1 withholds the right, and sends nothing (UI-BR-01)', async () => {
    let reached = 0;
    server.use(
      http.post('/api/v1/projects/:id/production-exemptions', () => {
        reached += 1;
        return HttpResponse.json({ id: 'EX-nope' }, { status: 201 });
      }),
    );
    renderProject(<ProductionExemptions />, 'exemptions');
    await screen.findAllByText('regional reporting deadline');
    await userEvent.type(screen.getByLabelText(/^Reason/), 'another deadline');
    const grant = screen.getByRole('button', { name: 'Grant exemption' });
    // production_exemption.manage is unavailable for this project in the mock: a complete form
    // cannot enable a control S1 has refused (FR-UI-009)
    expect(grant).toBeDisabled();
    await userEvent.click(grant);
    expect(reached).toBe(0);
  });

  it('says the answer was empty instead of drawing a header over nothing (AsyncShell)', async () => {
    // The six administration tables used to render the same markup for "S1 refused" and "there is
    // nothing yet". The shell has its own test; this one is about the wiring — that the arm reaches
    // the screen a person is actually standing on.
    server.use(http.get('/api/v1/roles', () => HttpResponse.json({ items: [] })));
    renderPlatform(<RolesView />);
    expect(await screen.findByText(/no roles/i)).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Role' })).not.toBeInTheDocument();
  });
});
