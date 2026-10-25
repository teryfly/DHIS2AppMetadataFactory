import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';
import { StatusBadge } from '@/components/StatusBadge';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { CheckboxInput } from '@/components/form';
import { ACTION } from '@/api/actions';
import { projectsListOptions } from '@/api/queries';
import { deliveryModeLabel, milestoneLabel, workflowStatusLabel } from '@/lib/labels';

/** UI-PRJ-001: projects as progress cards, with the next place to go visible on each one. */
export function ProjectsList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [showArchived, setShowArchived] = useState(false);
  const projects = useQuery(projectsListOptions(showArchived));
  const items = projects.data?.items ?? [];

  return (
    <PageContainer
      title={t('projects.title')}
      description={t('page.projects')}
      helpKey="projects"
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
      <div className="toolbar">
        <div className="toolbar__group">
          <CheckboxInput
            label={t('projects.includeArchived')}
            checked={showArchived}
            onChange={setShowArchived}
          />
        </div>
        <span className="result-count">
          {t('common.rowCount', { count: (projects.data as { total?: number } | undefined)?.total ?? items.length })}
        </span>
      </div>

      {projects.isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState
          icon="folder"
          title={t('projects.empty')}
          action={
            <Link to="/getting-started" className="secondary-link">
              {t('projects.openSetup')}
            </Link>
          }
          secondaryAction={
            <Link to="/projects/new" className="primary-link">
              {t('projects.newProject')}
            </Link>
          }
        />
      ) : (
        <div className="project-grid">
          {items.map((project) => {
            const stages = project.stages ?? [];
            const relevant = stages.filter((stage) => stage.status !== 'SKIPPED');
            const done = relevant.filter((stage) =>
              ['PASSED', 'APPROVED', 'VERIFIED', 'COMPLETED'].includes(String(stage.status ?? '')),
            ).length;
            const progress = relevant.length ? Math.round((done / relevant.length) * 100) : 0;
            const current = stages.find((stage) => stage.id === project.currentStageId);
            return (
              <article className="project-card" key={project.id}>
                <div className="project-card__top">
                  <div>
                    <h2 className="project-card__title">{project.name}</h2>
                    {project.archived ? (
                      <span className="chip" style={{ marginTop: 6 }}>
                        {t('projects.archived')}
                      </span>
                    ) : null}
                  </div>
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
                <div className="project-card__meta">
                  {t('workspace.contextStage')}:{' '}
                  {current
                    ? t(`stageTypes.${current.stageType}`, { defaultValue: current.stageType })
                    : '—'}
                  {project.targetEnvironments?.length
                    ? ` · ${t('workspace.contextEnvironment')}: ${project.targetEnvironments.length}`
                    : ''}
                </div>
                <div className="progress" aria-label={t('dashboard.projectProgress')}>
                  <div
                    className={`progress__bar${progress === 100 ? ' progress__bar--success' : ''}`}
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <div className="project-card__footer">
                  <span className="project-card__meta">{progress}%</span>
                  <Link
                    to={`/projects/${encodeURIComponent(project.id ?? '')}/overview`}
                    className="secondary-link"
                  >
                    {t('projects.open')}
                  </Link>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}
