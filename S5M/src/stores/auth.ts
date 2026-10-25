import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import type { QueryClient } from '@tanstack/vue-query';
import { apiGet, apiSend } from '@/api/http';
import { AppError } from '@/api/errors';
import { activeQueryClient } from '@/api/queryClient';
import { dismissErrors } from '@/lib/toast';
import { leaveTo } from '@/lib/leave';
import { oidcRedirectUri, safeReturnTo } from '@/lib/origin';
import { SS, clearSessionText, readSessionText, writeSessionText } from '@/lib/storage';
import type { OidcRequestView, SessionView } from '@/api/types';

// Session identity/permission state comes from S1 only (UM-P-01). Auth is cookie-based, so the
// browser carries the session; we keep just the last-read SessionView in memory.
export const useAuthStore = defineStore('auth', () => {
  const session = ref<SessionView | null>(null);
  const loading = ref(false);

  const authMode = computed(() => session.value?.authMode ?? 'off');
  const authenticated = computed(() => !!session.value?.authenticated);
  const needsLogin = computed(() => authMode.value !== 'off' && !authenticated.value);
  const mustChangePassword = computed(() => !!session.value?.mustChangePassword);
  const roles = computed<string[]>(() => session.value?.roles ?? []);
  const subject = computed(() => session.value?.subject ?? '');
  const userId = computed(() => session.value?.userId ?? '');

  /** Whether this deployment signs people in through the organisation (UM-AUTH-001). */
  const oidcAvailable = computed(() => authMode.value === 'oidc' || authMode.value === 'both');
  /** In flight for either half of the single sign-on round trip. */
  const oidcBusy = ref(false);
  /** A `code` is a single-use credential; this route must never offer the same one twice. */
  const oidcSpent = ref(false);

  /**
   * Why the last sign-in did not work, kept as state rather than as a toast (UM-AUTH-001).
   *
   * A toast is gone in three seconds and, while the sign-in dialog is open, it can sit under the
   * modal overlay — the operator taps 登录, the dialog stays, and nothing on screen says either
   * "working" or "refused, because". The screen where they acted is the screen that has to answer.
   */
  const signInFailure = ref<{ code: string; message: string; correlationId?: string } | null>(null);

  /**
   * The same kind of answer for the *single sign-on* half of the screen (UM-AUTH-001).
   *
   * Separate because the two refusals mean different things and the advice under a password failure
   * is about passwords: "five wrong ones freeze this account" is noise under "the organisation's
   * sign-in endpoint did not answer", and pasting them together would say something the server
   * never said.
   */
  const oidcFailure = ref<{ code: string; message: string; correlationId?: string } | null>(null);

  /**
   * Set when a session ended under the operator (idle timeout, or a password change that revoked it)
   * and cleared by the next sign-in. The sign-in page says so: being sent there mid-task with no
   * explanation reads as a crash, and "your session ended" is a different event from "sign in".
   */
  const sessionLapsed = ref(false);

  /** Keep the server's own words as the fallback, and hand the screen the code too. */
  function describe(error: unknown): { code: string; message: string; correlationId?: string } {
    if (error instanceof AppError) {
      return { code: error.errorCode, message: error.message, correlationId: error.correlationId };
    }
    return { code: 'NETWORK_ERROR', message: (error as Error)?.message ?? String(error) };
  }

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      session.value = await apiGet<SessionView>('/auth/session');
    } finally {
      loading.value = false;
    }
  }

  /**
   * Everything that is true the moment S1 says "this principal is here", whichever door they came
   * through — the password form or the organisation's single sign-on.
   */
  function afterSignIn(): void {
    sessionLapsed.value = false;
    // The console opens with the reads it made before there was a session, and each one answered
    // 401; that wall of red describes a state this sign-in has just ended.
    dismissErrors();
    // Every cached read so far was answered for whoever was signed in before, so none of it may
    // outlive the sign-in: re-ask S1 now instead of waiting for the next reload (UM-P-01).
    void activeQueryClient()?.invalidateQueries();
  }

  async function login(subj: string, password: string): Promise<void> {
    signInFailure.value = null;
    try {
      session.value = await apiSend<SessionView>('POST', '/auth/login', { subject: subj, password });
    } catch (error) {
      // `UNAUTHENTICATED` is what S1 answers for both "no such account" and "wrong password", and
      // that equivalence is deliberate (总体 SRS 21.4) — the console must not invent a distinction
      // it cannot see. The code goes to the screen; the typed password never leaves this call.
      signInFailure.value = describe(error);
      throw error;
    }
    afterSignIn();
  }

  /**
   * Send the browser to the organisation's own sign-in page (UM-AUTH-001).
   *
   * `state` and `nonce` are minted by S1 and come back on an HttpOnly cookie, so this app never
   * holds them — which is what makes a forged callback fail. The request is made per click and
   * never cached, because that pair is single-use: a second trip with a used pair is a refusal.
   */
  async function startOidc(returnTo = '/'): Promise<void> {
    oidcBusy.value = true;
    oidcFailure.value = null;
    try {
      const request = await apiGet<OidcRequestView>('/auth/oidc/request', {
        query: { redirectUri: oidcRedirectUri() },
      });
      // The IdP returns only `code` and `state`, so wherever the operator was heading has to be
      // held on this side of the redirect. Session-scoped, and read back once.
      writeSessionText(SS.oidcReturn, safeReturnTo(returnTo));
      const target = new URL(request.authorizationEndpoint);
      target.searchParams.set('client_id', request.clientId);
      target.searchParams.set('redirect_uri', request.redirectUri);
      target.searchParams.set('response_type', request.responseType);
      target.searchParams.set('scope', request.scope);
      target.searchParams.set('state', request.state);
      target.searchParams.set('nonce', request.nonce);
      leaveTo(target.toString());
    } catch (error) {
      oidcFailure.value = describe(error);
    } finally {
      oidcBusy.value = false;
    }
  }

  /**
   * Trade the `code` the organisation handed the browser for a session (UM-AUTH-001).
   *
   * `state` goes back as a query parameter because that is the only place S1 compares it with the
   * cookie it minted. The guard matters more here than on the password form: a code is spent the
   * first time it is used, so a re-entry into this route — a back navigation, a second mount — must
   * not offer the same credential twice.
   */
  async function exchangeOidc(code: string, state: string): Promise<void> {
    if (oidcSpent.value) return;
    oidcSpent.value = true;
    oidcBusy.value = true;
    signInFailure.value = null;
    try {
      session.value = await apiSend<SessionView>(
        'POST',
        '/auth/oidc/exchange',
        { code, redirectUri: oidcRedirectUri() },
        undefined,
        { query: { state } },
      );
      afterSignIn();
    } catch (error) {
      // A refusal here is a sign-in refusal, so it belongs in the same slot the password form uses —
      // the callback page words it itself, because none of it is about a wrong password.
      signInFailure.value = describe(error);
    } finally {
      oidcBusy.value = false;
    }
  }

  /** The screen the operator was on before the redirect, once, and never again. */
  function takeOidcReturn(): string {
    const path = readSessionText(SS.oidcReturn);
    if (path) clearSessionText(SS.oidcReturn);
    return safeReturnTo(path);
  }

  /**
   * A read answered 401: whoever the app thinks is here is not who S1 still recognises.
   *
   * Kept as one deliberate state change instead of a toast per failed screen. `authMode` survives —
   * it is a property of the deployment, and clearing the whole session would flip it to `off`,
   * which is the one answer that hides the sign-in panel on an account deployment. Cached reads go
   * the way `logout` sends them (drop them, do not re-ask): the next principal has to re-read them,
   * and while nobody is signed in the answers would all be 401.
   */
  function markLapsed(): void {
    const last = session.value;
    if (!last || last.authenticated === false) return;
    signInFailure.value = null;
    sessionLapsed.value = true;
    session.value = {
      ...last,
      authenticated: false,
      subject: '',
      displayName: '',
      roles: [],
      userId: undefined,
      expiresAt: undefined,
      mustChangePassword: false,
    };
    void activeQueryClient()?.invalidateQueries({ refetchType: 'none' });
  }

  async function logout(): Promise<void> {
    await apiSend('POST', '/auth/logout', {});
    signInFailure.value = null;
    oidcFailure.value = null;
    // An explicit sign-out is not "your session ended on its own": the page must not say that.
    sessionLapsed.value = false;
    await refresh();
    // Drop the previous principal's data, but do not re-ask for it: with no session the next
    // answer is a 401 per open screen, and the sign-in panel is already up.
    void activeQueryClient()?.invalidateQueries({ refetchType: 'none' });
  }

  return {
    session, loading, authMode, authenticated, needsLogin, mustChangePassword, roles, subject, userId,
    signInFailure, sessionLapsed, oidcAvailable, oidcBusy, oidcFailure,
    refresh, login, logout, markLapsed, startOidc, exchangeOidc, takeOidcReturn,
  };
});

/**
 * Watch the caches for a session that died while a page was open (UM-NET-002).
 *
 * This is wired from `main.ts` rather than done inside `api/queryClient.ts`, because that module is
 * already imported by the store below — a store that reaches into the client that reaches back into
 * the store is a cycle for no benefit. The cache is where the failure actually surfaces (a read or a
 * write answered 401), and the session is whose state that invalidates, so the two are joined here
 * instead of by a hook each screen would have to remember to install.
 */
export function attachSessionLapseWatch(client: QueryClient): () => void {
  const auth = useAuthStore();
  const lapse = (error: unknown): void => {
    if (error instanceof AppError && error.isUnauthenticated) auth.markLapsed();
  };
  const queries = client.getQueryCache().subscribe((event) => {
    if (event.type === 'updated') lapse(event.query.state.error);
  });
  const mutations = client.getMutationCache().subscribe((event) => {
    if (event.type === 'updated') lapse(event.mutation.state.error);
  });
  return () => {
    queries();
    mutations();
  };
}
