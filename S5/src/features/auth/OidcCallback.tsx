/*
 * UI-AUTH-001: the landing half of the single sign-on round trip.  The browser comes back with a
 * code and the state cookie S1 minted; S1 checks the pair, so nothing here has to remember it.
 *
 * The exchange fires once per code: React's development double-invoke would otherwise send the
 * same code twice and the second one is, correctly, a spent credential.
 */
import { useEffect, useRef } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useOidcExchange } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import { NoticeBar } from '@/components/NoticeBar';
import { PageContainer } from '@/lib/PageContainer';
import { pageOrigin } from '@/lib/origin';

export function OidcCallback() {
  const { t } = useTranslation();
  const location = useLocation();
  const origin = pageOrigin();
  const exchange = useOidcExchange();
  const sent = useRef(false);
  const code = new URLSearchParams(location.search).get('code');
  const denied = new URLSearchParams(location.search).get('error');

  useEffect(() => {
    if (!code || sent.current) return;
    sent.current = true;
    exchange.mutate({ code, redirectUri: `${origin}/auth/callback` });
  }, [code, origin, exchange]);

  if (denied) {
    return (
      <NoticeBar tone="error" title={t('auth.signInFailed')}>
        {denied}
      </NoticeBar>
    );
  }
  if (!code) {
    return (
      <NoticeBar tone="warning" title={t('auth.callbackMissing')}>
        <Link to="/login">{t('auth.signIn')}</Link>
      </NoticeBar>
    );
  }
  if (exchange.isError) {
    const error = exchange.error;
    return (
      <NoticeBar tone="error" title={t('auth.signInFailed')}>
        <div className="stack" style={{ gap: 4 }}>
          <span>
            {isApiError(error) ? translateErrorCode(error.errorCode, error.message) : String(error)}
          </span>
          <Link to="/login">{t('auth.signIn')}</Link>
        </div>
      </NoticeBar>
    );
  }
  // SessionGate put the interrupted screen in history state; ignoring it sent every SSO sign-in to
  // the dashboard. Same site-relative-only rule as the mobile sign-in page: `//host` and absolute
  // URLs are how a login page becomes an open redirect.
  const from = (location.state as { from?: string } | null)?.from ?? '';
  const returnTo = from.startsWith('/') && !from.startsWith('//') ? from : '/';
  if (exchange.isSuccess) return <Navigate to={returnTo} replace />;

  return <PageContainer title={t('auth.signInTitle')}>{<p className="muted">{t('auth.checking')}</p>}</PageContainer>;
}
