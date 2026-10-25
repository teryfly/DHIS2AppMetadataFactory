/*
 * UI-AUTH-001: sign in (FR-SEC-001).  Local account and password here, the organisation's single
 * sign-on one call away; the OIDC request is fetched per click rather than through a query,
 * because its `state`/`nonce` pair is a one-time value and a cached copy would be a spent one.
 *
 * No credential value is ever written to a browser store (FR-SEC-007): the password goes into the
 * request and nowhere else, and the session arrives as an HttpOnly cookie the page cannot read.
 */
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@dhis2/ui';
import { client, unwrap } from '@/api/client';
import { sessionOptions } from '@/api/queries';
import { useSignIn } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import { TextInput } from '@/components/form';
import { NoticeBar } from '@/components/NoticeBar';
import { Heading } from '@/components/typography';
import { PageContainer } from '@/lib/PageContainer';
import { pageOrigin } from '@/lib/origin';

interface OidcRequestView {
  authorizationEndpoint: string;
  clientId: string;
  redirectUri: string;
  responseType: string;
  scope: string;
  state: string;
  nonce: string;
}

export function LoginScreen() {
  const { t } = useTranslation();
  const location = useLocation();
  const nav = (location.state ?? {}) as { from?: string; lapsed?: boolean };
  const from = nav.from ?? '/';
  const session = useQuery(sessionOptions());
  const signIn = useSignIn();
  const [subject, setSubject] = useState('');
  const [password, setPassword] = useState('');
  const [oidcError, setOidcError] = useState<string | null>(null);
  const origin = pageOrigin();

  if (session.data?.authenticated || session.data?.authMode === 'off') {
    // nothing to sign in to, or already signed in: the gate would not have routed here, so a
    // direct visit goes back to where they were heading
    return <Navigate to={from} replace />;
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!subject.trim() || !password) return;
    // The typed password is the one value this screen must not keep showing after a refusal: it is
    // still on the operator's screen (and in the DOM) while they read the reason (FR-SEC-007).
    signIn.mutate({ subject: subject.trim(), password }, { onError: () => setPassword('') });
  };

  const startOidc = async () => {
    setOidcError(null);
    try {
      const request = await unwrap<OidcRequestView>(() =>
        client.GET('/api/v1/auth/oidc/request', {
          params: { query: { redirectUri: `${origin}/auth/callback` } },
        }),
      );
      const target = new URL(request.authorizationEndpoint);
      target.searchParams.set('client_id', request.clientId);
      target.searchParams.set('redirect_uri', request.redirectUri);
      target.searchParams.set('response_type', request.responseType);
      target.searchParams.set('scope', request.scope);
      target.searchParams.set('state', request.state);
      target.searchParams.set('nonce', request.nonce);
      window.location.assign(target.toString());
    } catch (error) {
      setOidcError(error instanceof Error ? error.message : String(error));
    }
  };

  const failure = signIn.error;
  const mode = session.data?.authMode ?? 'local';

  return (
    <PageContainer title={t('auth.signInTitle')}>
      <div className="stack" style={{ maxWidth: 420 }}>
        {nav.lapsed ? <NoticeBar tone="warning" title={t('auth.sessionExpired')} /> : null}
        <p className="muted">{t('auth.signInBody')}</p>

        {failure && isApiError(failure) ? (
          <NoticeBar tone="error" title={t('auth.signInFailed')}>
            <div className="stack" style={{ gap: 4 }}>
              <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
              {/* Said once, identically, whatever the refusal was: S1 answers "no such account" and
                  "wrong password" with the same words on purpose, and a console that guessed louder
                  than the server would reopen the enumeration channel it closed (CP-IAM-005 ④). */}
              <span className="muted">{t('auth.lockNote')}</span>
              {failure.correlationId ? (
                <span className="mono muted" style={{ fontSize: 12 }}>
                  {t('common.correlationId')}: {failure.correlationId}
                </span>
              ) : null}
            </div>
          </NoticeBar>
        ) : null}
        {oidcError ? <NoticeBar tone="error" title={t('auth.oidcUnavailable')}>{oidcError}</NoticeBar> : null}

        <form onSubmit={submit} className="stack" aria-label={t('auth.signInTitle')}>
          <TextInput label={t('auth.subject')} value={subject} onChange={setSubject} required type="text" autoComplete="username" />
          <TextInput
            label={t('auth.password')}
            value={password}
            onChange={setPassword}
            required
            type="password"
            autoComplete="current-password"
          />
          <div>
            <Button primary type="submit" disabled={!subject.trim() || !password || signIn.isPending}>
              {t('auth.signIn')}
            </Button>
          </div>
          {/* A dead button needs a reason. "Disabled" that says nothing is the same experience as a
              tap that does nothing, and the two are indistinguishable from the operator's side. */}
          {!subject.trim() || !password ? <p className="muted">{t('auth.needBoth')}</p> : null}
        </form>
        {signIn.isPending ? <p className="muted">{t('auth.checking')}</p> : null}

        {mode === 'oidc' || mode === 'both' ? (
          <div>
            <Button secondary onClick={startOidc}>
              {t('auth.signInWithOrganisation')}
            </Button>
          </div>
        ) : null}

        <Heading level={6} size="small">
          {t('auth.credentialNote')}
        </Heading>
      </div>
    </PageContainer>
  );
}
