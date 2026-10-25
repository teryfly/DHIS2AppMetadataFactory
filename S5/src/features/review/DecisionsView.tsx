/*
 * S5IF-BP13 as a page. The Registry row names one decision per object; until this existed, that
 * column's link landed on the router's catch-all — an expensive way of telling an operator "the
 * reason this object is shaped like this is not available to you".
 *
 * `?ids=` stays in the address so a review comment can name the decision instead of describing it,
 * for the same reason an artifact has its own URL (UI-REV-001).
 */
import { Link, useOutletContext, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { AsyncShell } from '@/components/AsyncShell';
import { decisionsOptions } from '@/api/queries';
import { DecisionCard } from './DecisionCard';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';

export function DecisionsView() {
  const { t } = useTranslation();
  const ctx = useOutletContext<WorkspaceContext>();
  const [params] = useSearchParams();
  const wanted = (params.getAll('ids') ?? []).flatMap((value) => value.split(',')).filter((value) => value !== '');
  const decisions = useQuery(decisionsOptions(ctx.projectId, wanted));
  const items = decisions.data?.items ?? [];

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>{t('decisions.title')}</h2>
          <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>{t('page.decisions')}</p>
        </div>
      </div>
      <p className="muted">{t('decisions.intro')}</p>
      {wanted.length ? (
        <p className="muted" style={{ fontSize: 12 }}>
          {t('decisions.onlyThese', { ids: wanted.join(', ') })} ·{' '}
          <Link to={`/projects/${encodeURIComponent(ctx.projectId)}/decisions`}>{t('decisions.showAll')}</Link>
        </p>
      ) : null}
      <AsyncShell
        loading={decisions.isLoading}
        error={decisions.error}
        empty={items.length === 0}
        emptyTitle={t('decisions.empty')}
        emptyHint={t('decisions.emptyHint')}
        onRetry={() => void decisions.refetch()}
      >
        {items.map((d) => <DecisionCard key={d.decisionId} decision={d} projectId={ctx.projectId} />)}
      </AsyncShell>
    </div>
  );
}
