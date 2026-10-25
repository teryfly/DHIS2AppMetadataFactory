import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { PageContainer } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';

/** 404 / unknown route. */
export function NotFound() {
  const { t } = useTranslation();
  return (
    <PageContainer title={t('common.notFound')}>
      <EmptyState
        icon="info"
        title="404"
        action={
          <Link to="/" className="primary-link">
            {t('notImplemented.back')}
          </Link>
        }
        secondaryAction={
          <Link to="/projects" className="secondary-link">
            {t('nav.projects')}
          </Link>
        }
      >
        {t('common.notFound')}
      </EmptyState>
    </PageContainer>
  );
}
