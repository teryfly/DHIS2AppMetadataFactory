/*
 * The approver's queue: every gate stopped for a person, across projects, at one address. The rows
 * are S1's list verbatim — its filter, its order, its page and its total.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@dhis2/ui';
import { PageContainer } from '@/lib/PageContainer';
import { AsyncShell } from '@/components/AsyncShell';
import { StatusBadge } from '@/components/StatusBadge';
import { SelectInput } from '@/components/form';
import { PAGE_SIZE, gatesInboxOptions, projectsListOptions } from '@/api/queries';
import { gateTypeLabel } from '@/lib/labels';
import { formatRelativeTime } from '@/lib/time';

export function ApprovalsInbox() {
  const { t } = useTranslation();
  const [mine, setMine] = useState(false);
  const [projectId, setProjectId] = useState('');
  const [offset, setOffset] = useState(0);
  const gates = useQuery(gatesInboxOptions({ mine, projectId: projectId || undefined, offset }));
  const projects = useQuery(projectsListOptions());
  const items = gates.data?.items ?? [];
  const total = gates.data?.total ?? items.length;

  const toggle = (next: { mine?: boolean; projectId?: string }) => {
    setMine((current) => next.mine ?? current);
    setProjectId((current) => next.projectId ?? current);
    setOffset(0);
  };

  return (
    <PageContainer title={t('approvals.title')} description={t('page.approvals')} helpKey="approvals">
      <div className="filter-bar">
        <SelectInput
          label={t('notifications.filterProject')}
          value={projectId}
          onChange={(value) => toggle({ projectId: value })}
          options={[
            { value: '', label: t('common.all') },
            ...(projects.data?.items ?? []).map((project) => ({
              value: project.id ?? '',
              label: project.name ?? project.id ?? '',
            })),
          ]}
        />
        <label className="filter-bar__check">
          <input
            type="checkbox"
            checked={mine}
            onChange={(event) => toggle({ mine: event.target.checked })}
          />{' '}
          {t('approvals.filterMine')}
        </label>
      </div>

      <AsyncShell
        loading={gates.isLoading}
        error={gates.error}
        empty={items.length === 0}
        emptyTitle={t('approvals.empty')}
        emptyHint={t('approvals.emptyHint')}
        onRetry={() => void gates.refetch()}
      >
        <p className="result-count" style={{ margin: '0 0 10px' }}>
          {t('approvals.shownOf', { shown: items.length, total })}
        </p>
        <div className="stack">
          {items.map((gate) => (
            <article className="project-card" key={gate.id}>
              <div className="project-card__top">
                <strong className="project-card__title">{gate.projectName ?? gate.projectId ?? '—'}</strong>
                <StatusBadge status={gate.status} />
              </div>
              <div className="project-card__meta">
                {gateTypeLabel(gate.type)}
                {gate.environmentKey ? ` · ${gate.environmentKey}` : ''}
                {gate.createdAt ? ` · ${formatRelativeTime(gate.createdAt, t)}` : ''}
              </div>
              <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                <span className="chip">{t('gate.approvalProgress', { done: gate.approvalCount, required: gate.requiredApprovals })}</span>
                <span className={gate.unresolvedWarningCount ? 'chip chip--warning' : 'chip'}>
                  {t('approvals.unresolvedWarnings', { n: gate.unresolvedWarningCount })}
                </span>
                <span className={gate.highOpenQuestionCount ? 'chip chip--danger' : 'chip'}>
                  {t('approvals.highOpenQuestions', { n: gate.highOpenQuestionCount })}
                </span>
              </div>
              {gate.projectId ? (
                <div className="project-card__footer" style={{ justifyContent: 'flex-end' }}>
                  <Link
                    to={`/projects/${encodeURIComponent(gate.projectId)}/gate/${encodeURIComponent(gate.id)}`}
                    className="primary-link"
                  >
                    {t('approvals.openGate')}
                  </Link>
                </div>
              ) : null}
            </article>
          ))}
          {items.length < total ? (
            <Button secondary small onClick={() => setOffset((current) => current + PAGE_SIZE)}>
              {t('common.loadMore')}
            </Button>
          ) : null}
        </div>
      </AsyncShell>
    </PageContainer>
  );
}
