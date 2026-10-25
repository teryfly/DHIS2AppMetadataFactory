import { Link } from 'react-router-dom';
import { Icon, type IconName } from './icons';

export interface RouteTab {
  key: string;
  to: string;
  label: string;
  icon?: IconName;
  badge?: number;
  active: boolean;
}

/**
 * Route tabs are navigation, not ARIA tabs: each one changes the address, so they are links with
 * `aria-current="page"`. In-page panels use a real tablist instead.
 */
export function RouteTabs({
  label,
  tabs,
  secondary = false,
  className,
}: {
  label: string;
  tabs: RouteTab[];
  secondary?: boolean;
  className?: string;
}) {
  return (
    <nav
      aria-label={label}
      className={`route-tabs${secondary ? ' route-tabs--secondary' : ''}${className ? ` ${className}` : ''}`}
    >
      {tabs.map((tab) => (
        <Link
          key={tab.key}
          to={tab.to}
          className={`route-tab${tab.active ? ' route-tab--active' : ''}`}
          aria-current={tab.active ? 'page' : undefined}
        >
          {tab.icon ? <Icon name={tab.icon} size={16} /> : null}
          <span>{tab.label}</span>
          {typeof tab.badge === 'number' && tab.badge > 0 ? (
            <span className="route-tab__badge">{tab.badge}</span>
          ) : null}
        </Link>
      ))}
    </nav>
  );
}
