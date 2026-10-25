import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useUiStore } from '@/stores/ui';
import { Icon } from './icons';

export interface Breadcrumb {
  label: string;
  to?: string;
}

export function PageHeader({
  title,
  description,
  eyebrow,
  breadcrumbs,
  actions,
  helpKey,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  breadcrumbs?: Breadcrumb[];
  actions?: ReactNode;
  helpKey?: string;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const openHelp = useUiStore((s) => s.openHelp);

  return (
    <header className="page-header">
      {breadcrumbs?.length ? (
        <nav className="page-header__crumbs" aria-label={t('nav.breadcrumb')}>
          {breadcrumbs.map((crumb, index) => (
            <span key={`${crumb.label}-${index}`} className="page-header__crumb">
              {crumb.to ? <Link to={crumb.to}>{crumb.label}</Link> : <span>{crumb.label}</span>}
              {index < breadcrumbs.length - 1 ? <Icon name="chevronRight" size={14} /> : null}
            </span>
          ))}
        </nav>
      ) : null}
      {eyebrow ? <div className="page-header__eyebrow">{eyebrow}</div> : null}
      <div className="page-header__main">
        <div className="page-header__copy">
          <h1 className="page-header__title">{title}</h1>
          {description ? <p className="page-header__description">{description}</p> : null}
        </div>
        <div className="page-header__actions">
          {actions}
          {helpKey ? (
            <button
              type="button"
              className="quiet-button"
              onClick={() => openHelp(helpKey)}
              aria-label={t('help.openPageHelp')}
            >
              <Icon name="help" size={17} />
              <span>{t('help.open')}</span>
            </button>
          ) : null}
        </div>
      </div>
      {children}
    </header>
  );
}
