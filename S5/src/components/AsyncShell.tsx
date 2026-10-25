/*
 * The desktop counterpart of the mobile `FuiAsync` shell: one place that decides what a list looks
 * like while it loads, when S1 refuses it, and when it comes back with nothing in it.
 *
 * Six tables (members, grants, exemptions, roles, accounts, deployments) shipped a header and an
 * empty `<tbody>` — which reads as a page that broke rather than as "there is nothing here yet", and
 * left a refused request with no row to show for it. The shell is what makes that answer the same
 * everywhere instead of one screen at a time.
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@dhis2/ui';
import { EmptyState } from './EmptyState';
import { NoticeBar } from './NoticeBar';
import { Loading } from '@/lib/PageContainer';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';

interface Props {
  loading: boolean;
  /** The query's error object; anything falsy means the read succeeded. */
  error?: unknown;
  empty?: boolean;
  emptyTitle?: string;
  emptyHint?: string;
  /** Usually the control that makes there be something; the shell never invents one. */
  emptyAction?: ReactNode;
  onRetry?: () => void;
  children: ReactNode;
}

export function AsyncShell({ loading, error, empty = false, emptyTitle, emptyHint, emptyAction, onRetry, children }: Props) {
  const { t } = useTranslation();

  if (loading) return <Loading />;

  if (error) {
    // UI-REQ-002's affordance, the same rule `ToastHost` applies: asking an operator to retry a read
    // S1 answered "this does not exist" teaches them to press a button that cannot work.
    const canRetry = onRetry && (isApiError(error) ? error.retryable : true);
    return (
      <NoticeBar tone="error" title={t('error.title')}>
        <div className="stack" style={{ gap: 8 }}>
          <span>{isApiError(error) ? translateErrorCode(error.errorCode, error.message) : String(error)}</span>
          {isApiError(error) && error.correlationId ? (
            <span className="mono muted" style={{ fontSize: 12 }}>
              {t('common.correlationId')}: {error.correlationId}
            </span>
          ) : null}
          {canRetry ? (
            <div>
              <Button secondary small onClick={onRetry}>
                {t('common.retry')}
              </Button>
            </div>
          ) : null}
        </div>
      </NoticeBar>
    );
  }

  if (empty) {
    return (
      <EmptyState action={emptyAction}>
        <strong>{emptyTitle}</strong>
        {emptyHint ? <span>&nbsp;{emptyHint}</span> : null}
      </EmptyState>
    );
  }

  return <>{children}</>;
}
