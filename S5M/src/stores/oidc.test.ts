/*
 * UM-AUTH-001: the single sign-on round trip, one click out and one exchange back.
 *
 * What the console is actually responsible for here is small and specific, and all of it is invisible
 * in a browser test: build the IdP URL out of what S1 returned (a missing `nonce` is a login that
 * fails at the provider, with an error message about neither party), send the `state` back as a query
 * parameter (that is the only place S1 compares it with the cookie it minted), offer a `code` exactly
 * once, and honour a return path only when it is a path inside this app.
 *
 * `@/lib/leave` is mocked rather than `window.location`, because that seam exists precisely so this
 * file can read where the operator was sent instead of watching jsdom refuse to navigate.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { apiGet, apiSend } from '@/api/http';
import { leaveTo } from '@/lib/leave';
import { useAuthStore } from './auth';
import { AppError } from '@/api/errors';
import type { OidcRequestView, SessionView } from '@/api/types';

vi.mock('@/api/http', () => ({ apiGet: vi.fn(), apiSend: vi.fn() }));
vi.mock('@/lib/leave', () => ({ leaveTo: vi.fn() }));

// Built from the test runner's own origin rather than written out: `isolation.test.ts` forbids an
// absolute URL literal anywhere in src, on purpose — and the property under test is that the store
// forwards whatever host S1 named, whatever it is.
const IDP = new URL('/fake-idp/authorize', window.location.origin).href;

const REQUEST: OidcRequestView = {
  authorizationEndpoint: IDP,
  clientId: 'factory',
  nonce: 'n-1',
  redirectUri: `${window.location.origin}/auth/callback`,
  responseType: 'code',
  scope: 'openid profile email',
  state: 's-1',
};

const SESSION = { authenticated: true, authMode: 'both', subject: 'zhangwei', displayName: '张伟', roles: [], mustChangePassword: false } as SessionView;

function withMode(mode: string | null): void {
  useAuthStore().session = { authenticated: false, mustChangePassword: false, ...(mode === null ? {} : { authMode: mode }) } as SessionView;
}

describe('single sign-on (UM-AUTH-001)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.mocked(apiGet).mockReset();
    vi.mocked(apiSend).mockReset();
    vi.mocked(leaveTo).mockReset();
    sessionStorage.clear();
  });

  it('offers the organisation’s door only when the deployment has one', () => {
    withMode('local');
    expect(useAuthStore().oidcAvailable).toBe(false);
    withMode('both');
    expect(useAuthStore().oidcAvailable).toBe(true);
    withMode('oidc');
    expect(useAuthStore().oidcAvailable).toBe(true);
    withMode(null);
    expect(useAuthStore().oidcAvailable).toBe(false);
  });

  it('sends the browser to the provider with every value S1 asked it to echo', async () => {
    vi.mocked(apiGet).mockResolvedValue(REQUEST);
    await useAuthStore().startOidc('/projects/proj-1');

    const url = new URL(vi.mocked(leaveTo).mock.calls[0]?.[0] ?? '');
    expect(url.origin + url.pathname).toBe(IDP);
    // snake_case is the provider’s spelling, not S1’s: the contract answers camelCase and the
    // redirect that fails silently is the one where the console mixed the two up.
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: 'factory',
      redirect_uri: `${window.location.origin}/auth/callback`,
      response_type: 'code',
      scope: 'openid profile email',
      state: 's-1',
      nonce: 'n-1',
    });
    // the request's own redirectUri is the callback URL, so S1 mints the state for the right trip
    expect(vi.mocked(apiGet).mock.calls[0]?.[1]).toEqual({ query: { redirectUri: `${window.location.origin}/auth/callback` } });
    expect(useAuthStore().oidcFailure).toBeNull();
  });

  it('keeps where the operator was heading, and only an in-app path', async () => {
    vi.mocked(apiGet).mockResolvedValue(REQUEST);
    const auth = useAuthStore();

    await auth.startOidc('/access');
    expect(auth.takeOidcReturn()).toBe('/access');
    // read once: a second trip through the callback must not resurrect a stale destination
    expect(auth.takeOidcReturn()).toBe('/');

    await auth.startOidc('//evil.example.org/x');
    expect(auth.takeOidcReturn()).toBe('/');
    // An absolute URL is the open-redirect shape; it is assembled here rather than written out so
    // this file keeps its one promise to `isolation.test.ts` (no address literal in src).
    await auth.startOidc(`${window.location.protocol}//evil.example.org/x`);
    expect(auth.takeOidcReturn()).toBe('/');
  });

  it('reports an unreachable provider without borrowing the password advice', async () => {
    vi.mocked(apiGet).mockRejectedValue(new AppError({ errorCode: 'PROVIDER_UNAVAILABLE', httpStatus: 503, message: 'no idp' }));
    const auth = useAuthStore();
    await auth.startOidc('/');

    expect(auth.oidcFailure?.code).toBe('PROVIDER_UNAVAILABLE');
    // the lock note is rendered under `signInFailure` only; a 503 from the provider is not evidence
    // about anybody's password, and the screen must not imply that it is.
    expect(auth.signInFailure).toBeNull();
    expect(leaveTo).not.toHaveBeenCalled();
  });

  it('echoes the state back the way S1 reads it, and signs the operator in', async () => {
    vi.mocked(apiSend).mockResolvedValue(SESSION);
    const auth = useAuthStore();
    await auth.exchangeOidc('code-1', 's-1');

    expect(vi.mocked(apiSend).mock.calls[0]).toEqual([
      'POST',
      '/auth/oidc/exchange',
      { code: 'code-1', redirectUri: `${window.location.origin}/auth/callback` },
      undefined,
      { query: { state: 's-1' } },
    ]);
    expect(auth.authenticated).toBe(true);
    expect(auth.sessionLapsed).toBe(false);
    expect(auth.signInFailure).toBeNull();
  });

  it('offers a code once: a re-entry does not spend the credential again', async () => {
    vi.mocked(apiSend).mockResolvedValue(SESSION);
    const auth = useAuthStore();
    await auth.exchangeOidc('code-1', 's-1');
    await auth.exchangeOidc('code-1', 's-1');
    expect(apiSend).toHaveBeenCalledTimes(1);
  });

  it('refuses to look like a login when the exchange was refused', async () => {
    vi.mocked(apiSend).mockRejectedValue(new AppError({ errorCode: 'CSRF_FAILED', httpStatus: 403, message: 'state mismatch' }));
    const auth = useAuthStore();
    await auth.exchangeOidc('code-1', 'other-state');

    expect(auth.signInFailure?.code).toBe('CSRF_FAILED');
    expect(auth.authenticated).toBe(false);
    expect(auth.oidcBusy).toBe(false);
  });
});
