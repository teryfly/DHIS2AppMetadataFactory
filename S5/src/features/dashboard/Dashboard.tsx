import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';
import { StatusBadge } from '@/components/StatusBadge';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { SectionCard, StatCard } from '@/components/SectionCard';
import { Icon } from '@/components/icons';
import { ACTION } from '@/api/actions';
import { SetupSummary } from '@/features/setup/SetupSummary';
import { projectsListOptions, gatesPendingOptions, recentRunsOptions, notificationsOptions } from '@/api/queries';
import { formatRelativeTime } from '@/lib/time';
import {
  gateTypeLabel,
  deliveryModeLabel,
  milestoneLabel,
  notificationTypeLabel,
  workflowStatusLabel,
} from '@/lib/labels';

function stageProgress(stages: Array<{ status?: string }> | undefined): number {
  const relevant = (stages ?? []).filter((stage) => stage.status !== 'SKIPPED');
  if (!relevant.length) return 0;
  const done = relevant.filter((stage) =>
    ['PASSED', 'APPROVED', 'VERIFIED', 'COMPLETED'].includes(String(stage.status ?? '')),
  ).length;
  return Math.round((done / relevant.length) * 100);
}

/** UI-PRJ-001: what needs a person first, then projects, runs and notifications. */
export function Dashboard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const projects = useQuery(projectsListOptions());
  const gates = useQuery(gatesPendingOptions());
  const runs = useQuery(recentRunsOptions());
  const notifications = useQuery(notificationsOptions(undefined, { unreadOnly: true, limit: 6 }));

  const projectItems = projects.data?.items ?? [];
  const gateItems = gates.data?.items ?? [];
  const runItems = runs.data?.items ?? [];
  const notificationItems = notifications.data?.items ?? [];
  const unread = notifications.data?.total ?? notificationItems.length;
  const projectTotal = (projects.data as { total?: number } | undefined)?.total ?? projectItems.length;

  return (
    <PageContainer
      title={t('dashboard.title')}
      description={t('page.dashboard')}
      helpKey="dashboard"
      actions={
        <AllowedActionButton
          workflowId={undefined}
          scope="platform"
          action={ACTION.projectCreate}
          onExecute={() => navigate('/projects/new')}
        >
          {t('projects.newProject')}
        </AllowedActionButton>
      }
    >
      <SetupSummary />

      <div className="stat-grid">
        <StatCard
          label={t('dashboard.statsProjects')}
          value={projectTotal}
          hint={t('dashboard.projectProgress')}
          icon="folder"
          tone="brand"
        />
        <StatCard
          label={t('dashboard.statsApprovals')}
          value={gateItems.length}
          hint={gateItems.length ? t('dashboard.needAttention') : t('dashboard.allClear')}
          icon="inbox"
          tone={gateItems.length ? 'warning' : 'success'}
        />
        <StatCard
          label={t('dashboard.statsRuns')}
          value={runItems.length}
          hint={t('dashboard.runsSection')}
          icon="rocket"
        />
        <StatCard
          label={t('dashboard.statsUnread')}
          value={unread}
          hint={t('dashboard.notificationsSection')}
          icon="bell"
          tone={unread ? 'warning' : 'success'}
        />
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-main">
          <SectionCard
            title={`${t('dashboard.needAttention')} · ${t('dashboard.gatesSection')}`}
            description={gateItems.length ? t('dashboard.allClearHint') : t('dashboard.allClear')}
            actions={
              gateItems.length ? (
                <Link to="/approvals" className="secondary-link">
                  {t('dashboard.viewAll')}
                </Link>
              ) : null
            }
          >
            {gates.isLoading ? (
              <Loading />
            ) : gateItems.length === 0 ? (
              <EmptyState icon="check" title={t('dashboard.noGates')}>
                {t('dashboard.allClearHint')}
              </EmptyState>
            ) : (
              <div className="action-list">
                {gateItems.map((gate) => (
                  <div className="action-item" key={gate.id}>
                    <span className="action-item__icon">
                      <Icon name="inbox" size={18} />
                    </span>
                    <div className="action-item__body">
                      <strong className="action-item__title">
                        {gate.projectName ?? gateTypeLabel(gate.type)}
                      </strong>
                      <span className="action-item__meta">
                        {t('dashboard.attentionGate', { type: gateTypeLabel(gate.type) })}
                        {gate.environmentKey ? ` · ${gate.environmentKey}` : ''}
                        {gate.createdAt ? ` · ${formatRelativeTime(gate.createdAt, t)}` : ''}
                      </span>
                      <span className="action-item__meta">
                        {t('dashboard.attentionWarnings', {
                          n: gate.unresolvedWarningCount ?? 0,
                          q: gate.highOpenQuestionCount ?? 0,
                        })}
                      </span>
                    </div>
                    <div className="action-item__cta">
                      <Link
                        to={`/projects/${encodeURIComponent(gate.projectId ?? '')}/gate/${encodeURIComponent(gate.id)}`}
                        className="primary-link"
                      >
                        {t('approvals.openGate')}
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard
            title={t('dashboard.projectsSection')}
            description={t('dashboard.projectProgress')}
            actions={
              <Link to="/projects" className="secondary-link">
                {t('dashboard.viewAll')}
              </Link>
            }
          >
            {projects.isLoading ? (
              <Loading />
            ) : projectItems.length === 0 ? (
              <EmptyState
                icon="rocket"
                title={t('dashboard.noProjects')}
                action={
                  <Link to="/projects/new" className="primary-link">
                    {t('projects.newProject')}
                  </Link>
                }
                secondaryAction={
                  <Link to="/getting-started" className="secondary-link">
                    {t('dashboard.setupOpen')}
                  </Link>
                }
              >
                {t('dashboard.startBody')}
              </EmptyState>
            ) : (
              <div className="project-grid">
                {projectItems.map((project) => {
                  const progress = stageProgress(project.stages);
                  return (
                    <article className="project-card" key={project.id}>
                      <div className="project-card__top">
                        <h3 className="project-card__title">{project.name}</h3>
                        <StatusBadge
                          status={project.workflowStatus ?? 'PENDING'}
                          label={workflowStatusLabel(project.workflowStatus ?? 'PENDING')}
                        />
                      </div>
                      <div className="project-card__meta">
                        {t('projects.milestone')}: {project.milestoneStatus ? milestoneLabel(project.milestoneStatus) : '—'}
                        {' · '}
                        {deliveryModeLabel(project.deliveryMode)}
                      </div>
                      <div className="progress" aria-label={t('dashboard.projectProgress')}>
                        <div className="progress__bar" style={{ width: `${progress}%` }} />
                      </div>
                      <div className="project-card__footer">
                        <span className="project-card__meta">{progress}%</span>
                        <Link to={`/projects/${encodeURIComponent(project.id ?? '')}/overview`} className="secondary-link">
                          {t('dashboard.openProject')}
                        </Link>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </SectionCard>
        </div>

        <aside className="dashboard-side">
          <SectionCard title={t('dashboard.runsSection')} description={t('page.deployments')}>
            {runs.isLoading ? (
              <Loading />
            ) : runItems.length === 0 ? (
              <EmptyState>{t('dashboard.noRuns')}</EmptyState>
            ) : (
              <ul className="issue-list" style={{ paddingLeft: 0, listStyle: 'none' }}>
                {runItems.slice(0, 5).map((run) => (
                  <li key={run.id} className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
                    <span>
                      <strong>{run.environmentKey}</strong>
                      <span className="muted"> · {run.blueprintVersion ?? '—'}</span>
                    </span>
                    <span className="row" style={{ gap: 8 }}>
                      <StatusBadge status={run.status ?? 'PENDING'} />
                      <span className="muted" style={{ fontSize: 11.5 }}>
                        {formatRelativeTime(run.lastVerifiedAt ?? run.startedAt, t)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard
            title={t('dashboard.notificationsSection')}
            actions={
              <Link to="/notifications" className="secondary-link">
                {t('dashboard.viewAll')}
              </Link>
            }
          >
            {notificationItems.length === 0 ? (
              <EmptyState>{t('dashboard.noNotifications')}</EmptyState>
            ) : (
              <ul className="issue-list" style={{ paddingLeft: 0, listStyle: 'none' }}>
                {notificationItems.slice(0, 6).map((notification) => (
                  <li key={notification.id}>
                    <strong>{notificationTypeLabel(notification.type)}</strong>
                    {notification.message ? (
                      <span className="muted"> · {notification.message}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </aside>
      </div>
    </PageContainer>
  );
}
