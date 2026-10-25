/*
 * UI-AUTH-001/002: the gate that decides whether the app tree may render at all.
 *
 * Three regimes, read from S1 rather than inferred (CP-IAM-001): `off` is the M1/M2 deployment and
 * renders exactly what it always did, an authenticated session renders the tree under that
 * identity, and anything else is sent to the sign-in screen with the place they were going kept
 * for the way back.  A failed session read that is *not* "no session" is an outage and says so —
 * showing a login form for a server that is down would invite the user to hand credentials to
 * nothing.
 */
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { sessionOptions } from '@/api/queries';
import { isApiError } from '@/api/errors';
import { useCsrfStore } from '@/stores/csrf';

export function SessionGate() {
  const { t } = useTranslation();
  const location = useLocation();
  const session = useQuery(sessionOptions());
  const view = session.data;

  // Render while the question is still open.  Blocking first paint on a session read would delay
  // every deployment for the sake of the one regime that uses accounts, and the reads that follow
  // answer 401 by themselves; what gates the tree is the *answer*, not the request.
  if (session.isPending) return <Outlet />;

  if (view?.authMode === 'off' || view?.authenticated) return <Outlet />;

  if (session.isError) {
    const error = session.error;
    const signedOut = isApiError(error) && error.isUnauthenticated;
    if (!signedOut) {
      return (
        <div role="alert" className="inline-note inline-note--danger" style={{ margin: 24 }}>
          <div>
            <strong>{t('auth.sessionUnavailable')}</strong>
            <div>{error instanceof Error ? error.message : String(error)}</div>
          </div>
        </div>
      );
    }
  }

  // `lapsed` distinguishes "signed out by the clock" from "never signed in here": only the first
  // deserves to be announced, and it is announced once, here, not per request (UI-AUTH-002)
  const lapsed = useCsrfStore.getState().lapsed;
  return (
    <Navigate
      to="/login"
      replace
      state={{ from: `${location.pathname}${location.search}`, lapsed }}
    />
  );
}
