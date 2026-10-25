import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { PageHeader, type Breadcrumb } from '@/components/PageHeader';

/**
 * Consistent page frame: breadcrumb/eyebrow, title, purpose sentence, actions and optional help.
 * The old two-argument call still works, so screens can be redesigned one at a time.
 */
export function PageContainer({
  title,
  actions,
  children,
  description,
  eyebrow,
  breadcrumbs,
  helpKey,
  tabs,
  aside,
  className,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  breadcrumbs?: Breadcrumb[];
  helpKey?: string;
  tabs?: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`page-root${className ? ` ${className}` : ''}`}>
      <PageHeader
        title={title}
        description={description}
        eyebrow={eyebrow}
        breadcrumbs={breadcrumbs}
        actions={actions}
        helpKey={helpKey}
      >
        {tabs}
      </PageHeader>
      {aside ? (
        <div className="content-grid content-grid--aside">
          <div className="content-grid__main">{children}</div>
          <aside className="content-grid__aside">{aside}</aside>
        </div>
      ) : (
        <div className="page__body">{children}</div>
      )}
    </div>
  );
}

export function Loading({ label }: { label?: string } = {}) {
  const { t } = useTranslation();
  return (
    <div className="loading" role="status" aria-busy="true">
      <span className="spinner" aria-hidden="true" />
      <span className="muted">{label ?? t('app.loading')}</span>
    </div>
  );
}
