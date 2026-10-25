import type { ReactNode } from 'react';
import { Icon, type IconName } from './icons';

/**
 * An empty state has to say what would appear here, why it is empty and where to go next.
 * The `action` slot is the way out; `secondaryAction` is the slower alternative.
 */
export function EmptyState({
  children,
  title,
  icon = 'info',
  action,
  secondaryAction,
}: {
  children?: ReactNode;
  title?: ReactNode;
  icon?: IconName;
  action?: ReactNode;
  secondaryAction?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon" aria-hidden="true">
        <Icon name={icon} size={22} />
      </span>
      <div className="empty-state__copy">
        {title ? <strong className="empty-state__title">{title}</strong> : null}
        {children ? <div className="empty-state__body">{children}</div> : null}
      </div>
      {action || secondaryAction ? (
        <div className="empty-state__actions">
          {action}
          {secondaryAction}
        </div>
      ) : null}
    </div>
  );
}
