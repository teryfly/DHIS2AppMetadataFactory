import { useTranslation } from 'react-i18next';
import { useConnectionStore } from '@/stores/connection';

/** UI-RT-005 / UI-AC-03: warn when the realtime link has been down >10s; recovers automatically. */
export function OfflineBanner() {
  const { t } = useTranslation();
  const status = useConnectionStore((s) => s.status);
  if (status === 'offline') {
    return (
      <div role="alert" className="inline-note inline-note--warning" style={{ marginBottom: 12 }}>
        {t('banner.offline')}
      </div>
    );
  }
  if (status === 'connecting') {
    return (
      <div className="inline-note" style={{ marginBottom: 12 }}>
        {t('banner.offlineReconnecting')}
      </div>
    );
  }
  return null;
}
