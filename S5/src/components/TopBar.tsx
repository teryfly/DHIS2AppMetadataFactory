import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { notificationsOptions, sessionOptions } from '@/api/queries';
import { useSignOut } from '@/api/mutations';
import { useOperatorStore } from '@/stores/operator';
import { useUiStore } from '@/stores/ui';
import { LanguageSwitch } from './LanguageSwitch';
import { roleLabel } from '@/lib/labels';
import { ShellIcon } from './shellIcons';

/** App top bar: brand, help, notifications, language and the operator's own actions. */
export function TopBar() {
  const { t } = useTranslation();
  const operator = useOperatorStore((s) => s.name);
  const openOperator = useUiStore((s) => s.openOperatorDialog);
  const openPassword = useUiStore((s) => s.openPasswordDialog);
  const openHelp = useUiStore((s) => s.openHelp);
  const { data } = useQuery(notificationsOptions(undefined, { unreadOnly: true, limit: 1 }));
  const unread = data?.total ?? 0;
  const { data: who } = useQuery(sessionOptions());
  const signOut = useSignOut();
  const authenticated = !!who?.authenticated;
  const displayName = who?.displayName || who?.subject || operator;

  return (
    <>
    <header className="topbar">
      <Link to="/" className="topbar__brand">
        <span className="topbar__brand-mark" aria-hidden="true">
          <ShellIcon name="workflow" size={18} />
        </span>
        <span>{`${t('app.brand')} · ${t('app.subtitle')}`}</span>
      </Link>

      <div className="topbar__spacer" />

      <div className="topbar__actions">
        <button
          type="button"
          className="topbar__icon-button"
          onClick={() => openHelp()}
          aria-label={t('help.open')}
        >
          <ShellIcon name="help" size={19} />
        </button>

        <Link
          to="/notifications"
          className="topbar__action"
          aria-label={`${t('nav.notifications')}${unread ? ` (${unread})` : ''}`}
        >
          <ShellIcon name="bell" size={18} />
          <span>{t('nav.notifications')}</span>
          {unread > 0 ? <span className="topbar__badge">{unread}</span> : null}
        </Link>

        <LanguageSwitch />

        {authenticated ? (
          <>
            <span
              className="topbar__user topbar__user--static"
              title={who?.roles?.length ? who.roles.map(roleLabel).join(' · ') : undefined}
            >
              <ShellIcon name="users" size={16} />
              <span className="topbar__user-name">{displayName}</span>
            </span>
            <button
              type="button"
              className="topbar__button"
              onClick={openPassword}
            >
              {who?.mustChangePassword ? `⚠ ${t('auth.changeEntry')}` : t('auth.changeEntry')}
            </button>
            <button
              type="button"
              className="topbar__button"
              onClick={() => signOut.mutate()}
              aria-label={t('auth.signOut')}
            >
              <ShellIcon name="lock" size={16} />
              <span>{t('auth.signOut')}</span>
            </button>
          </>
        ) : (
          <button type="button" className="topbar__user" onClick={openOperator} aria-label={t('operator.change')}>
            <ShellIcon name="users" size={16} />
            <span className="topbar__user-name">
              {operator ? `${t('operator.label')}: ${operator}` : `⚠ ${t('operator.missing')}`}
            </span>
          </button>
        )}
      </div>
    </header>
    {who?.mustChangePassword ? (
      <div className="app-banner" role="status">
        <span>{t('auth.changeRequired')}</span>
        <button type="button" className="quiet-button" onClick={openPassword}>
          {t('auth.changeEntry')}
        </button>
      </div>
    ) : null}
    </>
  );
}
