import type { ReactNode } from 'react';
import { Icon, type IconName } from './icons';

export function SectionCard({
  title,
  description,
  actions,
  children,
  footer,
  tone = 'default',
  className,
  titleId,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  tone?: 'default' | 'warning' | 'danger' | 'brand';
  className?: string;
  titleId?: string;
}) {
  return (
    <section
      className={`card card--${tone}${className ? ` ${className}` : ''}`}
      aria-labelledby={titleId}
    >
      {title || actions ? (
        <header className="card__header">
          <div className="card__heading">
            {title ? <h2 className="card__title" id={titleId}>{title}</h2> : null}
            {description ? <p className="card__description">{description}</p> : null}
          </div>
          {actions ? <div className="card__actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="card__body">{children}</div>
      {footer ? <footer className="card__footer">{footer}</footer> : null}
    </section>
  );
}

export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = 'default',
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  icon?: IconName;
  tone?: 'default' | 'brand' | 'warning' | 'danger' | 'success';
}) {
  return (
    <div className={`stat-card stat-card--${tone}`}>
      <div className="stat-card__top">
        <span className="stat-card__label">{label}</span>
        {icon ? <Icon name={icon} size={18} /> : null}
      </div>
      <div className="stat-card__value">{value}</div>
      {hint ? <div className="stat-card__hint">{hint}</div> : null}
    </div>
  );
}

export function ContentGrid({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className={`content-grid${aside ? ' content-grid--aside' : ''}`}>
      <div className="content-grid__main">{children}</div>
      {aside ? <aside className="content-grid__aside">{aside}</aside> : null}
    </div>
  );
}

export function InlineNote({
  icon = 'info',
  children,
  tone = 'default',
}: {
  icon?: IconName;
  children: ReactNode;
  tone?: 'default' | 'warning' | 'danger';
}) {
  return (
    <div className={`inline-note inline-note--${tone}`}>
      <Icon name={icon} size={18} />
      <div>{children}</div>
    </div>
  );
}
