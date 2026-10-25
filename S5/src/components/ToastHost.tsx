import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { useToastStore, type Toast } from './toast';
import { onApiError } from '@/api/queryClient';
import { translateErrorCode } from '@/i18n';

const TONE_COLOR: Record<Toast['tone'], string> = {
  info: '#404b4c',
  success: '#469546',
  warning: '#df8b08',
  error: '#de350b',
};

/**
 * Global API-error → toast bridge (UI-REQ-002). A retryable error is given the retry affordance;
 * COMMIT network failures are handled separately in the console and never auto-resubmit here.
 */
export function ToastHost() {
  const { t } = useTranslation();
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  const push = useToastStore((s) => s.push);
  const queryClient = useQueryClient();

  // Retrying means asking the cache to re-read; the failing request itself is a query the operator
  // was looking at, and this is the same path SSE invalidation takes.
  const retry = (id: string) => {
    void queryClient.invalidateQueries();
    dismiss(id);
  };

  useEffect(
    () =>
      onApiError((error) => {
        // Operator-required is surfaced inline by buttons / the operator gate, not as a toast.
        if (error.isOperatorRequired) return;
        // UI-AUTH-002: an expired session hits every in-flight request of the page at once, so one
        // notice from the session gate replaces a toast per request. A CSRF refusal is shown by the
        // control that got it, with its correlationId — repeating it as a toast asks the user to
        // retry something S1 has already refused.
        // A 401's consequence — re-ask who this is — belongs to the cache layer (UI-AUTH-002);
        // here it is simply not another toast.
        if (error.isUnauthenticated || error.isCsrfFailed) return;
        push({
          tone: 'error',
          title: t('error.title'),
          body: translateErrorCode(error.errorCode, error.message),
          correlationId: error.correlationId,
          retryable: error.retryable,
        });
      }),
    [push, t],
  );

  if (toasts.length === 0) return null;

  return (
    <div className="toast-host" role="region" aria-live="assertive" aria-label={t('error.title')}>
      {toasts.map((toast) => (
        <div key={toast.id} role="alert" style={{ borderLeft: `6px solid ${TONE_COLOR[toast.tone]}`, background: '#fff', boxShadow: '0 2px 8px rgba(0,0,0,.18)', borderRadius: 4, padding: '10px 12px' }}>
          <div className="stack" style={{ gap: 2 }}>
            <strong>{toast.title}</strong>
            {toast.body ? <span>{toast.body}</span> : null}
            {toast.correlationId ? (
              <span className="mono muted" style={{ fontSize: 12 }}>
                {t('common.correlationId')}: {toast.correlationId}
              </span>
            ) : null}
          </div>
          <div className="row" style={{ marginTop: 8, justifyContent: 'flex-end', gap: 8 }}>
            {/* UI-REQ-002 promised this affordance; the store carried `retryable` but the render only
                ever offered Close, so a dropped read had no way back except reloading the page. */}
            {toast.retryable ? (
              <button type="button" onClick={() => retry(toast.id)}>{t('common.retry')}</button>
            ) : null}
            <button type="button" onClick={() => dismiss(toast.id)}>{t('common.close')}</button>
          </div>
        </div>
      ))}
    </div>
  );
}
