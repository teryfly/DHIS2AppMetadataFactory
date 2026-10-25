/*
 * S5IF-G04: the long lists are read from the server, in pages, and the screen says which page it
 * is showing.
 *
 * Each case below is a claim the console used to make silently. An audit trail of 61 records asked
 * for once answers 50, and a screen that draws those 50 without a number is telling the operator
 * "these are the records"; a notification badge computed from the first page tells a person with 200
 * unread that they have 50. Both are wrong in the direction that looks right, which is the only
 * direction that survives a demo.
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

const page = (items: unknown[], nextCursor: string | null, total: number) =>
  HttpResponse.json({ items, total, limit: items.length, offset: 0, nextCursor });

const record = (id: string) => ({ id, eventType: `cp.thing.${id}`, operatorName: 'Ana', occurredAt: '2026-03-04T09:00:00Z' });

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('the audit trail is walked by cursor (UI-OPS-002)', () => {
  it('asks for the next page with the cursor S1 handed back, and appends rather than replaces', async () => {
    const asked: (string | null)[] = [];
    server.use(
      http.get('/api/v1/projects/:id/audit', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        asked.push(cursor);
        return cursor
          ? page([record('A-51')], null, 61)
          : page([record('A-1'), record('A-2')], 'c-2', 61);
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/audit`);
    await screen.findByText('cp.thing.A-1');
    expect(screen.getByText(/2 of 61/)).toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: /Load more/i }));
    await waitFor(() => expect(asked).toContain('c-2'));
    // Both pages on screen, one number above them.
    await screen.findByText('cp.thing.A-51');
    expect(screen.getByText('cp.thing.A-1')).toBeInTheDocument();
    expect(await screen.findByText(/3 of 61/)).toBeInTheDocument();
  });

  it('offers no continuation when S1 says there is none', async () => {
    server.use(
      http.get('/api/v1/projects/:id/audit', () => page([record('A-1')], null, 1)),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/audit`);
    await screen.findByText('cp.thing.A-1');
    expect(screen.queryByRole('button', { name: /Load more/i })).not.toBeInTheDocument();
  });
});

describe('notifications filter where the filtering lives (UI-OPS-003)', () => {
  const notice = (id: string, read: boolean) => ({ id, type: 'GATE_PENDING', message: `notice ${id}`, read, createdAt: '2026-03-04T09:00:00Z' });

  it('sends 只看未读 to S1 instead of hiding rows from the page it got', async () => {
    const urls: string[] = [];
    server.use(
      http.get('/api/v1/notifications', ({ request }) => {
        urls.push(new URL(request.url).search);
        const unread = new URL(request.url).searchParams.get('unreadOnly');
        return HttpResponse.json({
          items: unread === 'true' ? [notice('N-1', false)] : [notice('N-1', false), notice('N-2', true)],
          total: unread === 'true' ? 1 : 2,
          limit: 1,
          offset: 0,
          nextCursor: null,
        });
      }),
    );
    renderAt('/notifications');
    await screen.findByText('notice N-1');
    await screen.findByText('notice N-2');

    await userEvent.click(screen.getByRole('checkbox', { name: /Unread only/i }));
    await waitFor(() => expect(urls.some((u) => u.includes('unreadOnly=true'))).toBe(true));
    await screen.findByText('notice N-1');
    expect(screen.queryByText('notice N-2')).not.toBeInTheDocument();
  });

  it('sends 发给我的 to S1 as well', async () => {
    const urls: string[] = [];
    server.use(
      http.get('/api/v1/notifications', ({ request }) => {
        urls.push(new URL(request.url).search);
        return HttpResponse.json({ items: [notice('N-1', false)], total: 1, limit: 50, offset: 0, nextCursor: null });
      }),
    );
    renderAt('/notifications');
    await userEvent.click(await screen.findByRole('checkbox', { name: /Addressed to me/i }));
    await waitFor(() => expect(urls.some((u) => u.includes('mine=true'))).toBe(true));
  });
});

describe('the unread badge is S1’s count (UI-OPS-003)', () => {
  it('reads total from the unread-filtered request, not the length of a page', async () => {
    server.use(
      http.get('/api/v1/notifications', ({ request }) => {
        const params = new URL(request.url).searchParams;
        // 200 unread, but a page of one — the number on screen must be 200.
        if (params.get('unreadOnly') === 'true') {
          return HttpResponse.json({ items: [{ id: 'N-1', type: 'GATE_PENDING', message: 'one', read: false, createdAt: '2026-03-04T09:00:00Z' }], total: 200, limit: 1, offset: 0, nextCursor: null });
        }
        return HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0, nextCursor: null });
      }),
    );
    renderAt('/');
    const link = await screen.findByRole('link', { name: /Notifications.*200/i });
    expect(link).toBeInTheDocument();
    expect(within(link).getByText('200')).toBeInTheDocument();
  });
});

describe('the account list is paged (UI-AUTH-003)', () => {
  const account = (n: number) => ({ id: `USR-${n}`, subject: `user${n}@example.org`, displayName: `User ${n}`, source: 'local', status: 'ACTIVE', locked: false, mustChangePassword: false, roles: [] });

  it('asks for the next window when the operator moves on, and names the total', async () => {
    const offsets: string[] = [];
    server.use(
      http.get('/api/v1/users', ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get('offset') ?? '0');
        offsets.push(String(offset));
        return HttpResponse.json({
          items: [account(offset + 1), account(offset + 2)],
          total: 120, limit: 2, offset, nextCursor: null,
        });
      }),
    );
    renderAt('/users');
    // The login name appears twice on this screen — in the table and in the "assign a role"
    // picker — so the lookups are plural by design.
    await vi.waitFor(() => expect(screen.getAllByText('user1@example.org').length).toBeGreaterThan(0));
    // The count is S1's, not the length of what arrived.
    expect(screen.getByText(/2 of 120 accounts/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^Next/ }));
    await waitFor(() => expect(offsets).toContain('50'));
  });
});
