/*
 * UM-NET-002 / UI-AUTH-002 parity: a session that ends while a screen is open has to end *visibly*,
 * and once.
 *
 * Before this the mobile app had no 401 path at all: `needsLogin` only changed when something
 * re-read `/auth/session`, so an expired session left the shell claiming an identity that every
 * write was refusing — and each open screen's read added its own toast, turning one event into a
 * wall of red. The fix is one state change plus one watch, so that is what is asserted here: the
 * signal is the cache, the answer is the sign-in panel.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { QueryClient } from '@tanstack/vue-query';
import { useAuthStore, attachSessionLapseWatch } from './auth';
import { AppError } from '@/api/errors';
import type { SessionView } from '@/api/types';

function signedIn(): void {
  useAuthStore().session = {
    authenticated: true,
    authMode: 'local',
    subject: 'zhangwei',
    displayName: '张伟',
    userId: 'usr-1',
    roles: ['PLATFORM_ADMINISTRATOR'],
    mustChangePassword: false,
    expiresAt: '2026-10-05T02:00:00Z',
  } as SessionView;
}

async function rejected(client: QueryClient, status: number): Promise<void> {
  await client
    .fetchQuery({
      queryKey: [`probe-${status}`],
      // retry: 0 — the default retry would run this three times and the watch would fire twice
      retry: 0,
      queryFn: () =>
        Promise.reject(new AppError({ errorCode: 'UNAUTHENTICATED', httpStatus: status, message: 'no' })),
    })
    .catch(() => undefined);
}

describe('a lapsed session (UM-NET-002)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('clears the identity but keeps the deployment’s auth mode, so the panel appears', () => {
    signedIn();
    const auth = useAuthStore();
    expect(auth.needsLogin).toBe(false);

    auth.markLapsed();

    expect(auth.authenticated).toBe(false);
    // `authMode: 'off'` is the one answer that hides the sign-in panel on an account deployment —
    // nulling the whole session would silently produce it
    expect(auth.authMode).toBe('local');
    expect(auth.needsLogin).toBe(true);
    expect(auth.subject).toBe('');
    expect(auth.roles).toEqual([]);
    expect(auth.userId).toBe('');
  });

  it('says it once: a second lapse does not re-open anything already open', () => {
    signedIn();
    const auth = useAuthStore();
    auth.markLapsed();
    const after = auth.session;
    auth.markLapsed();
    // the guard returns early, so the object is the same one — not a fresh copy of "not signed in"
    expect(auth.session).toBe(after);
  });

  it('is driven by the cache, not by a hook each screen must remember', async () => {
    signedIn();
    const client = new QueryClient();
    const detach = attachSessionLapseWatch(client);

    await rejected(client, 401);
    expect(useAuthStore().authenticated).toBe(false);

    detach();
  });

  it('leaves any other refusal alone — a 403 is a permission answer, not a lost session', async () => {
    signedIn();
    const client = new QueryClient();
    const detach = attachSessionLapseWatch(client);

    await rejected(client, 403);
    expect(useAuthStore().authenticated).toBe(true);

    detach();
  });
});
