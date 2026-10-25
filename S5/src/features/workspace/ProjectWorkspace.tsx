/*
 * UI-WB-001: the project shell. One scroll container, one stage flow, five primary sections and
 * only the secondary tabs the current section needs. The old layout put a second sidebar and an
 * always-open inspector beside the page, which left roughly 520px for the work itself.
 */
import { useState } from 'react';
import { Link, Outlet, useLocation, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Loading } from '@/lib/PageContainer';
import { NoticeBar } from '@/components/NoticeBar';
import { StatusBadge } from '@/components/StatusBadge';
import { RouteTabs, type RouteTab } from '@/components/RouteTabs';
import { WorkflowStepper } from '@/components/WorkflowStepper';
import { Icon } from '@/components/icons';
import { StageChatPanel, hasDialogue } from './StageChatPanel';
import type { WorkspaceContext } from './WorkspaceContext';
import { useProjectWorkflow } from '@/hooks/useProjectWorkflow';
import {
  PROJECT_SECTIONS,
  PROJECT_SUBTABS,
  projectSectionForPath,
  projectSectionHref,
  projectSubTabForPath,
  projectSubTabHref,
} from '@/navigation/ia';
import { workflowGatesOptions } from '@/api/queries';
import { deliveryModeLabel, gateTypeLabel, milestoneLabel, workflowStatusLabel } from '@/lib/labels';
import type { StageView as StageViewType } from '@/api/types';

export function ProjectWorkspace() {
  const { projectId = '' } = useParams();
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const [chatOpen, setChatOpen] = useState(false);
  const { project, workflow, workflowId, currentStageId, stages } = useProjectWorkflow(projectId);
  const gates = useQuery(workflowGatesOptions(workflowId));

  if (project.isLoading || workflow.isLoading) return <Loading />;
  if (!project.data) return <NoticeBar tone="error">{t('errors.NOT_FOUND')}</NoticeBar>;

  const section = projectSectionForPath(pathname, projectId);
  const activeSubTab = projectSubTabForPath(pathname, projectId);
  const currentStage = stages.find((s) => s.id === currentStageId) as StageViewType | undefined;
  const onStageRoute = pathname.includes('/stages/');
  const chatable = onStageRoute && hasDialogue(currentStage?.stageType);
  const waitingGate = (gates.data?.items ?? []).find((gate) => gate.status === 'WAITING_APPROVAL');

  const primaryTabs: RouteTab[] = PROJECT_SECTIONS.map((entry) => ({
    key: entry.key,
    to: projectSectionHref(entry.key, projectId, currentStageId),
    label: t(entry.labelKey),
    icon: entry.icon,
    active: entry.key === section,
  }));

  const secondaryTabs: RouteTab[] = PROJECT_SUBTABS[section].map((tab) => ({
    key: tab.key,
    to: projectSubTabHref(section, tab, projectId, currentStageId),
    label: t(tab.labelKey),
    active: tab.key === activeSubTab,
  }));

  const nextAction = waitingGate
    ? {
        to: `/projects/${encodeURIComponent(projectId)}/gate/${encodeURIComponent(waitingGate.id)}`,
        label: `${t('gate.title')} · ${gateTypeLabel(waitingGate.type)}`,
      }
    : currentStage
      ? {
          to: `/projects/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(currentStage.id)}`,
          label: t('workspace.openStage'),
        }
      : null;

  return (
    <div className="page-root">
      <header className="project-header">
        <nav className="page-header__crumbs" aria-label={t('nav.breadcrumb')}>
          <span className="page-header__crumb">
            <Link to="/projects">{t('nav.projects')}</Link>
            <Icon name="chevronRight" size={14} />
          </span>
          <span className="page-header__crumb">{project.data.name}</span>
        </nav>

        <div className="project-header__top">
          <div>
            <div className="project-header__title-row">
              <h1 className="project-header__title">{project.data.name}</h1>
              <StatusBadge
                status={project.data.workflowStatus ?? 'PENDING'}
                label={workflowStatusLabel(project.data.workflowStatus ?? 'PENDING')}
              />
            </div>
            <div className="project-header__meta">
              <span className="chip">
                {t('projects.milestone')}: {project.data.milestoneStatus ? milestoneLabel(project.data.milestoneStatus) : '—'}
              </span>
              <span className="chip">{deliveryModeLabel(project.data.deliveryMode)}</span>
              <span className="chip">
                {t('workspace.contextStage')}:{' '}
                {currentStage ? t(`stageTypes.${currentStage.stageType}`, { defaultValue: currentStage.stageType }) : '—'}
              </span>
              {project.data.targetEnvironments?.length ? (
                <span className="chip">
                  {t('workspace.contextEnvironment')}: {project.data.targetEnvironments.length}
                </span>
              ) : null}
            </div>
          </div>

          <div className="project-header__actions">
            {nextAction ? (
              <Link to={nextAction.to} className="primary-link">
                <Icon name="play" size={15} />
                {nextAction.label}
              </Link>
            ) : null}
            <Link
              to={`/projects/${encodeURIComponent(projectId)}/settings`}
              className="quiet-button"
            >
              <Icon name="settings" size={15} />
              {t('projectShell.settings')}
            </Link>
          </div>
        </div>
      </header>

      <WorkflowStepper
        projectId={projectId}
        stages={stages as StageViewType[]}
        currentStageId={currentStageId}
        activeStageId={onStageRoute ? pathname.split('/').pop() : undefined}
      />

      <div className="project-tabs">
        <RouteTabs label={t('nav.projectSections')} tabs={primaryTabs} />
      </div>

      {secondaryTabs.length > 1 ? (
        <div className="project-subtabs">
          <RouteTabs label={t('nav.projectViews')} tabs={secondaryTabs} secondary />
        </div>
      ) : null}

      <div className="page__body">
        <Outlet
          context={{ projectId, workflowId, project: project.data, workflow: workflow.data } satisfies WorkspaceContext}
        />
      </div>

      {chatable && currentStageId ? (
        <>
          {chatOpen ? (
            <div className="chat-drawer">
              <StageChatPanel key={currentStageId} stageId={currentStageId} workflowId={workflowId} />
            </div>
          ) : null}
          <button
            type="button"
            className="chat-fab"
            aria-expanded={chatOpen}
            onClick={() => setChatOpen((value) => !value)}
          >
            <Icon name="sparkles" size={17} />
            {t('projectShell.assistant')}
          </button>
        </>
      ) : null}
    </div>
  );
}
