/*
 * UI-DEP-002: the deployment console for one run — environment & class, DHIS2 version, profile;
 * connection / permission / drift / snapshot; static + DHIS2 validation + approval progress;
 * create / update / delete / out-of-scope counts; maintenance actions; and COMMIT / cancel.
 * UI-DEP-004: COMMIT shows a second confirmation listing environment, payload hash and change stats.
 * UI-REQ-004 / UI-BR-05 / UI-AC-07: a COMMIT network failure does NOT auto-resubmit; the user
 * retries explicitly under the SAME idempotency key while the outcome is "pending confirmation".
 */
import { useState } from 'react';
import { useParams, useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card, Button, Modal, ModalTitle, ModalContent, ModalActions, NoticeBox, LinearLoader } from '@dhis2/ui';
import { StatusBadge } from '@/components/StatusBadge';
import { NoticeBar } from '@/components/NoticeBar';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { Loading } from '@/lib/PageContainer';
import { Heading } from '@/components/typography';
import { runSummaryOptions } from '@/api/queries';
import { useCancelDeploymentRun, useCommitRun } from '@/api/mutations';
import { actionIntent } from '@/api/idempotency';
import { isApiError } from '@/api/errors';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';
import { BindingPanel } from './BindingPanel';
import { OutcomePanel } from './OutcomePanel';

export function RunConsole() {
  const { runId = '' } = useParams();
  const ctx = useOutletContext<WorkspaceContext>();
  const { projectId, workflowId } = ctx;
  const { t } = useTranslation();
  const summary = useQuery(runSummaryOptions(runId));
  const commit = useCommitRun(workflowId ?? '', runId, projectId);
  const cancel = useCancelDeploymentRun(workflowId ?? '');
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [unconfirmed, setUnconfirmed] = useState(false);

  const intent = actionIntent('POST', '/deployment-runs/commit', runId);

  if (summary.isLoading) return <Loading />;
  const s = summary.data;
  if (!s) return <NoticeBar tone="error">{t('errors.NOT_FOUND')}</NoticeBar>;

  const doCommit = () => {
    setUnconfirmed(false);
    commit.mutate({ intent }, {
      onSuccess: () => setConfirmOpen(false),
      onError: (error) => {
        if (isApiError(error) && error.networkError) setUnconfirmed(true); // UI-AC-07
      },
    });
  };

  const stats = (s.changeStats ?? {}) as Record<string, unknown>;

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <Heading level={3} size="medium">{t('deployments.console')} · {s.environmentKey}</Heading>
        <div className="row" style={{ gap: 8 }}>
          <StatusBadge status={s.status} />
          {/* The run had a cancel endpoint and no way to reach it from the console: an operator
              watching an import go wrong could only wait for it. S1 answers 409 once the run is
              COMMITTING, which is why this is gated rather than judged from the badge. */}
          <AllowedActionButton
            workflowId={workflowId}
            action={ACTION.deploymentCancel}
            targetId={runId}
            tone="destructive"
            loading={cancel.isPending}
            onExecute={() => cancel.mutate({ runId })}
          >
            {t('deployments.cancelRun')}
          </AllowedActionButton>
        </div>
      </div>

      {/* UI-REQ-004 pending-confirmation panel */}
      {unconfirmed ? (
        <NoticeBar tone="warning" title={t('error.commitUnconfirmedTitle')}>
          <div className="stack">
            <span>{t('error.commitUnconfirmedBody')}</span>
            {/* FR-UI-009: even the explicit retry asks S1 first -- an outcome that has since
                been confirmed server-side must not stay clickable here. */}
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.executionCommit}
              targetId={runId}
              tone="primary"
              loading={commit.isPending}
              onExecute={() => commit.mutate(
                { intent },
                { onError: (e) => { if (!(isApiError(e) && e.networkError)) setUnconfirmed(false); } },
              )}
            >
              {t('common.retry')}
            </AllowedActionButton>
          </div>
        </NoticeBar>
      ) : null}

      <div className="grid-cards">
        <Card>
          <div className="stack" style={{ gap: 6 }}>
            <Heading level={5} size="small">{t('gate.affectsEnvironment')}</Heading>
            <span>{t(`environments.class${s.environmentClass ?? 'TEST'}`, { defaultValue: s.environmentClass ?? '' })}</span>
            <span className="muted">DHIS2 {s.dhis2Version ?? '—'} · Profile {s.profileVersion ?? '—'}</span>
          </div>
        </Card>
        <Card>
          <div className="stack" style={{ gap: 6 }}>
            <Heading level={5} size="small">{t('environments.connectionResult')}</Heading>
            <span>{t('deployments.connection')}: <StatusBadge status={(s.connection as { ok?: boolean } | null | undefined) == null ? 'PENDING' : (s.connection as { ok?: boolean }).ok ? 'PASSED' : 'FAILED'} /></span>
            <span>{t('deployments.validation')}: <StatusBadge status={(s.validation as { staticOk?: boolean; dhis2Ok?: boolean } | null | undefined) == null ? 'PENDING' : (s.validation as { dhis2Ok?: boolean }).dhis2Ok ? 'VALIDATED' : 'FAILED'} /></span>
            <span>{t('deployments.approval')}: <StatusBadge status={(s.approval as { deployGate?: string })?.deployGate ?? 'WAITING_APPROVAL'} /></span>
            <LinearLoader amount={approvalProgress(s.approval as { approvals?: number; requiredApprovals?: number })} />
          </div>
        </Card>
        <Card>
          <div className="stack" style={{ gap: 6 }}>
            <Heading level={5} size="small">{t('gate.willDoTitle')}</Heading>
            <span className="mono">create {num(stats.create)} · update {num(stats.update)} · delete {num(stats.delete)}</span>
            {s.outdated ? <span className="text-warning">{t('deployments.outdated')}</span> : null}
          </div>
        </Card>
      </div>

      <BindingPanel runId={runId} workflowId={workflowId ?? ''} />

      <OutcomePanel summary={s} />

      <Card>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <span className="muted">{t('gate.willHappenTitle')}</span>
          {/* FR-UI-009 / UI-BR-01: COMMIT is clickable only when S1 lists execution.commit.
              An unapproved DEPLOY_GATE must never be reachable by a local judgement. */}
          <AllowedActionButton
            workflowId={workflowId}
            action={ACTION.executionCommit}
            targetId={runId}
            tone="destructive"
            loading={commit.isPending}
            onExecute={() => setConfirmOpen(true)}
          >
            {t('deployments.commit')}
          </AllowedActionButton>
        </div>
      </Card>

      {confirmOpen ? (
        <Modal position="middle" hide={false} onClose={() => setConfirmOpen(false)}>
          <ModalTitle>{`${t('deployments.commit')} · ${t('common.confirm')}`}</ModalTitle>
          <ModalContent>
            {/* UI-DEP-004 second confirmation */}
            <div className="stack">
              <span>{t('gate.affectsEnvironment')}: <strong>{s.environmentKey}</strong></span>
              <span className="mono">payload: {(s.payload as { hash?: string })?.hash ?? s.blueprintVersion ?? '—'}</span>
              <span className="mono">create {num(stats.create)} · update {num(stats.update)} · delete {num(stats.delete)}</span>
              {commit.isPending ? <NoticeBox>{t('common.save')}…</NoticeBox> : null}
            </div>
          </ModalContent>
          <ModalActions>
            <Button secondary onClick={() => setConfirmOpen(false)}>{t('common.cancel')}</Button>
            <Button destructive onClick={doCommit} loading={commit.isPending}>{t('deployments.commit')}</Button>
          </ModalActions>
        </Modal>
      ) : null}
    </div>
  );
}

function approvalProgress(a?: { approvals?: number; requiredApprovals?: number }): number {
  if (!a?.requiredApprovals) return 0;
  return Math.min(100, Math.round(((a.approvals ?? 0) / a.requiredApprovals) * 100));
}
function num(v: unknown): number {
  return typeof v === 'number' ? v : 0;
}
