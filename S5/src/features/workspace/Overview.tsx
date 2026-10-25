/*
 * UI-PRJ-003 / AC-WF-01: the project's control centre. It answers four questions at a glance —
 * where the workflow stands, what needs a person, what the project targets, and which whole-project
 * controls are available. The stage sequence itself is the stepper above, not a second copy here.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Modal, ModalTitle, ModalContent, ModalActions } from '@dhis2/ui';
import { SectionCard } from '@/components/SectionCard';
import { StatusBadge } from '@/components/StatusBadge';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { EmptyState } from '@/components/EmptyState';
import { Icon } from '@/components/icons';
import { ACTION } from '@/api/actions';
import { workflowGatesOptions } from '@/api/queries';
import { useWorkflowFlag } from '@/api/mutations';
import { deliveryModeLabel, gateTypeLabel, milestoneLabel } from '@/lib/labels';
import type { ProjectView } from '@/api/types';

interface Ctx {
  projectId: string;
  workflowId?: string;
  project: ProjectView;
  workflow?: {
    currentStageId?: string;
    paused?: boolean;
    cancelled?: boolean;
    stages?: Array<{ id: string; stageType: string; status: string }>;
  };
}

export function Overview() {
  const { t } = useTranslation();
  const ctx = useOutletContext<Ctx>();
  const stages = ctx.workflow?.stages ?? ctx.project.stages ?? [];
  const currentId = ctx.workflow?.currentStageId ?? ctx.project.currentStageId;
  const current = stages.find((stage) => stage.id === currentId);
  const nextStage = stages.find((stage) =>
    ['READY', 'RUNNING', 'WAITING_INPUT', 'MANUAL_CHECK'].includes(String(stage.status ?? '')),
  );
  const gates = useQuery(workflowGatesOptions(ctx.workflowId));
  const waiting = (gates.data?.items ?? []).filter((gate) => gate.status === 'WAITING_APPROVAL');

  return (
    <div className="content-grid content-grid--aside">
      <div className="content-grid__main" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <SectionCard
          title={t('dashboard.nextStep')}
          description={t('workspace.currentStageHint')}
          actions={
            current ? (
              <Link
                to={`/projects/${encodeURIComponent(ctx.projectId)}/stages/${encodeURIComponent(current.id)}`}
                className="primary-link"
              >
                <Icon name="play" size={15} />
                {t('workspace.openStage')}
              </Link>
            ) : null
          }
        >
          {current ? (
            <div className="stack" style={{ gap: 10 }}>
              <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                <strong style={{ fontSize: 18 }}>
                  {t(`stageTypes.${current.stageType}`, { defaultValue: current.stageType })}
                </strong>
                <StatusBadge status={current.status} />
              </div>
              {nextStage && nextStage.id !== current.id ? (
                <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
                  {t('workspace.nextStageHint')} — {t(`stageTypes.${nextStage.stageType}`, { defaultValue: nextStage.stageType })}
                </p>
              ) : null}
            </div>
          ) : (
            <EmptyState icon="workflow" title={t('projectShell.noStages')}>
              {t('workspace.currentStageHint')}
            </EmptyState>
          )}
        </SectionCard>

        <SectionCard
          title={t('gate.title')}
          description={t('page.gate')}
          actions={
            waiting.length ? (
              <Link to="/approvals" className="secondary-link">
                {t('dashboard.viewAll')}
              </Link>
            ) : null
          }
        >
          {waiting.length === 0 ? (
            <EmptyState icon="check" title={t('dashboard.allClear')}>
              {t('dashboard.allClearHint')}
            </EmptyState>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              {waiting.map((gate) => (
                <div
                  className="action-item"
                  key={gate.id}
                  style={{ borderBottom: '1px solid var(--ds-border)', paddingBottom: 10 }}
                >
                  <span className="action-item__icon">
                    <Icon name="inbox" size={18} />
                  </span>
                  <div className="action-item__body">
                    <strong className="action-item__title">{gateTypeLabel(gate.type)}</strong>
                    <span className="action-item__meta">
                      {t('gate.warningsTitle')}: {gate.warnings?.length ?? 0}
                    </span>
                  </div>
                  <Link
                    to={`/projects/${encodeURIComponent(ctx.projectId)}/gate/${encodeURIComponent(gate.id)}`}
                    className="primary-link"
                  >
                    {t('approvals.openGate')}
                  </Link>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      <aside className="content-grid__aside">
        <SectionCard title={t('workspace.overviewTab')} description={t('page.overview')}>
          <dl className="stack" style={{ gap: 10, margin: 0 }}>
            <MetaRow label={t('projects.milestone')} value={ctx.project.milestoneStatus ? milestoneLabel(ctx.project.milestoneStatus) : '—'} />
            <MetaRow label={t('projects.deliveryMode')} value={deliveryModeLabel(ctx.project.deliveryMode) || '—'} />
            <MetaRow
              label={t('workspace.contextBlueprint')}
              value={ctx.project.currentBlueprintVersion ?? t('workspace.contextDraft')}
            />
            <MetaRow
              label={t('workspace.contextEnvironment')}
              value={
                ctx.project.targetEnvironments
                  ?.map((environment) => environment.environmentKey)
                  .filter(Boolean)
                  .join(', ') || '—'
              }
            />
          </dl>
        </SectionCard>

        {ctx.workflowId ? (
          <WorkflowControls
            workflowId={ctx.workflowId}
            projectId={ctx.projectId}
            paused={ctx.workflow?.paused === true}
            cancelled={ctx.workflow?.cancelled === true}
          />
        ) : null}
      </aside>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="muted" style={{ fontSize: 11.5 }}>
        {label}
      </dt>
      <dd style={{ margin: '2px 0 0', fontSize: 13 }}>{value}</dd>
    </div>
  );
}

/**
 * Pause / resume / cancel for the whole workflow. Which of pause and resume is offered follows
 * `paused` (the state the operator is acting on), but neither opens on that reading alone:
 * availability comes from allowed-actions, so a paused workflow whose resume S1 has refused stays
 * refused (FR-UI-009). Cancel is the one that cannot be undone, so it asks first.
 */
function WorkflowControls({
  workflowId,
  projectId,
  paused,
  cancelled,
}: {
  workflowId: string;
  projectId: string;
  paused: boolean;
  cancelled: boolean;
}) {
  const { t } = useTranslation();
  const flag = useWorkflowFlag(workflowId, projectId);
  const [confirmCancel, setConfirmCancel] = useState(false);

  if (cancelled) {
    return <SectionCard title={t('workspace.cancelWorkflow')}><p className="muted">{t('workspace.workflowCancelled')}</p></SectionCard>;
  }

  return (
    <SectionCard title={t('workspace.pauseWorkflow')} description={t('workspace.cancelWorkflowBody')}>
      <div className="form-actions">
        <AllowedActionButton
          workflowId={workflowId}
          action={paused ? ACTION.workflowResume : ACTION.workflowPause}
          tone="secondary"
          loading={flag.isPending}
          onExecute={() => flag.mutate({ flag: paused ? 'resume' : 'pause' })}
        >
          {paused ? t('workspace.resumeWorkflow') : t('workspace.pauseWorkflow')}
        </AllowedActionButton>
        <AllowedActionButton
          workflowId={workflowId}
          action={ACTION.workflowCancel}
          tone="destructive"
          onExecute={() => setConfirmCancel(true)}
        >
          {t('workspace.cancelWorkflow')}
        </AllowedActionButton>
      </div>

      {confirmCancel ? (
        <Modal position="middle" hide={false} onClose={() => setConfirmCancel(false)}>
          <ModalTitle>{`${t('workspace.cancelWorkflow')} · ${t('common.confirm')}`}</ModalTitle>
          <ModalContent>
            <span>{t('workspace.cancelWorkflowBody')}</span>
          </ModalContent>
          <ModalActions>
            <Button secondary onClick={() => setConfirmCancel(false)}>{t('common.cancel')}</Button>
            <Button
              destructive
              loading={flag.isPending}
              onClick={() => flag.mutate({ flag: 'cancel' }, { onSuccess: () => setConfirmCancel(false) })}
            >
              {t('workspace.cancelWorkflow')}
            </Button>
          </ModalActions>
        </Modal>
      ) : null}
    </SectionCard>
  );
}
