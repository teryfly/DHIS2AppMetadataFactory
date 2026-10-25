import { useTranslation } from 'react-i18next';
import { Link, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { gatesInboxOptions } from '@/api/queries';
import { useUiStore } from '@/stores/ui';
import { GLOBAL_NAV, isGlobalNavItemActive } from '@/navigation/ia';
import { ShellIcon } from './shellIcons';

/** Primary navigation: three groups, eight destinations, a live approval count and one help entry. */
export function SidebarNav() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const openHelp = useUiStore((s) => s.openHelp);
  // The approvals page already reads its own queue; asking again beside it duplicates the request
  // and races the page's first read. Elsewhere the badge is a useful glance at the queue size.
  const gates = useQuery({
    ...gatesInboxOptions({ mine: true, offset: 0 }),
    enabled: !pathname.startsWith('/approvals'),
  });
  const waiting = gates.data?.total ?? 0;

  return (
    <nav className="app-sidebar" aria-label={t('nav.mainMenu')}>
      <div className="sidebar__nav">
        {GLOBAL_NAV.map((group) => (
          <div key={group.labelKey} className="sidebar__group">
            <div className="sidebar__group-label">{t(group.labelKey)}</div>
            {group.items.map((item) => {
              const active = isGlobalNavItemActive(item, pathname);
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className="sidebar__item"
                  aria-current={active ? 'page' : undefined}
                >
                  <span className="sidebar__icon" aria-hidden="true">
                    <ShellIcon name={item.icon} size={18} />
                  </span>
                  <span className="sidebar__label">{t(item.labelKey)}</span>
                  {item.badge === 'approvals' && waiting > 0 ? (
                    <span className="sidebar__badge" aria-label={t('nav.waitingCount', { count: waiting })}>
                      {waiting}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </div>

      <div className="sidebar__footer">
        <button type="button" className="sidebar__help" onClick={() => openHelp()}>
          <span className="sidebar__icon" aria-hidden="true">
            <ShellIcon name="help" size={18} />
          </span>
          <span className="sidebar__label">{t('nav.help')}</span>
        </button>
        <div className="sidebar__version">{t('app.subtitle')}</div>
      </div>
    </nav>
  );
}
