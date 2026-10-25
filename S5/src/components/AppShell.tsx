import { lazy, Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useUiStore } from '@/stores/ui';
import { Loading } from '@/lib/PageContainer';
import { TopBar } from './TopBar';
import { SidebarNav } from './SidebarNav';
import { OfflineBanner } from './OfflineBanner';
import { OperatorDialog } from './OperatorDialog';
import { ChangePasswordDialog } from './ChangePasswordDialog';

// The guide is read on demand, so its code is not part of the first screen.
const HelpDrawer = lazy(() => import('./HelpDrawer').then((module) => ({ default: module.HelpDrawer })));

/**
 * Application chrome (UI-IA-001, UI-APP-001/003): skip link, top bar, grouped primary nav,
 * an offline banner from the live transport (UI-RT-005), the help drawer and the routed content.
 * There is one scroll container — the main region — so no nested pane can squeeze or overlap a page.
 */
export function AppShell() {
  const { t } = useTranslation();
  const helpOpen = useUiStore((s) => s.helpOpen);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        {t('a11y.skipToContent')}
      </a>
      <TopBar />
      <div className="app-body">
        <SidebarNav />
        <main id="main" className="app-main" tabIndex={-1} aria-label={t('a11y.mainContent')}>
          <OfflineBanner />
          <Suspense fallback={<Loading />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      {helpOpen ? (
        <Suspense fallback={null}>
          <HelpDrawer />
        </Suspense>
      ) : null}
      <OperatorDialog />
      <ChangePasswordDialog />
    </div>
  );
}
