/*
 * UI-GATE-001..006: the approval page answers six questions in reading order on the left, while the
 * decision itself — every warning, the confirmations, the approval progress, approve and reject —
 * stays in one sticky panel on the right. Approval availability still comes only from
 * allowed-actions; the local rules only add constraints.
 */
import { useMemo, useState } from 'react';
import { useParams, useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, NoticeBox } from '@dhis2/ui';
import { StatusBadge } from '@/components/StatusBadge';
import { NoticeBar } from '@/components/NoticeBar';
import { Loading } from '@/lib/PageContainer';
import { SectionCard } from '@/components/SectionCard';
import { CheckboxInput, TextAreaInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { gateOptions, gateExplanationOptions } from '@/api/queries';
import { DecisionCard } from '@/features/review/DecisionCard';
import { decisionText } from '@/lib/format';
import { useApproveGate, useRejectGate } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { gateTypeLabel } from '@/lib/labels';
import type { WorkspaceContext } from '../WorkspaceContext';
import type { GateWarningView } from '@/api/types';
import { refTypeLabel, severityLabel, stageTypeLabel, statusLabel } from '@/lib/labels';

type Disposition = 'ACCEPT' | 'FIX';

function warningKey(warning: GateWarningView, index: number): string {
  return warning.issueId ?? warning.businessKey ?? `w-${index}`;
}

export function GatePage() {
  const { gateId = '' } = useParams();
  const ctx = useOutletContext<WorkspaceContext>();
  const { projectId, workflowId } = ctx;
  const { t } = useTranslation();
  const gateQuery = useQuery(gateOptions(gateId));
  const approve = useApproveGate(workflowId ?? '', projectId);
  const reject = useRejectGate(workflowId ?? '');

  const [dispositions, setDispositions] = useState<Record<string, Disposition>>({});
  const [confirmations, setConfirmations] = useState<Record<string, boolean>>({});
  const [rejectReason, setRejectReason] = useState('');

  const gate = gateQuery.data;
  const expl = useQuery(gateExplanationOptions(gateId)).data;
  const warnings = gate?.warnings ?? [];
  const confirmationItems = gate?.confirmationItems ?? [];
  const foundItems = expl?.found?.boundObjects?.length
    ? expl.found.boundObjects.map((bound) => `${refTypeLabel(bound.refType)} · ${bound.refId}`)
    : warnings.map((warning) => warning.message);
  const uncertainItems = expl?.uncertain?.openQuestions?.length
    ? expl.uncertain.openQuestions.map((openQuestion) => {
        const value = openQuestion as { text?: unknown; question?: unknown };
        return typeof value.text === 'string'
          ? value.text
          : typeof value.question === 'string'
            ? value.question
            : JSON.stringify(openQuestion);
      })
    : warnings.map((warning) => warning.message);
  const whyItems = Object.values(expl?.why ?? {}).flat();
  const canChangeItems = (expl?.canChange ?? []).map((entry) => decisionText(entry));

  const allWarningsHandled = useMemo(
    () => warnings.every((warning, index) => dispositions[warningKey(warning, index)]),
    [warnings, dispositions],
  );
  const anyNeedsFix = useMemo(
    () => Object.values(dispositions).some((disposition) => disposition === 'FIX'),
    [dispositions],
  );
  const allConfirmed = confirmationItems.every((item) => !item.required || confirmations[item.id]);
  const canApprove = allWarningsHandled && !anyNeedsFix && allConfirmed;

  if (gateQuery.isLoading) return <Loading />;
  if (!gate) return <NoticeBar tone="error">{t('errors.NOT_FOUND')}</NoticeBar>;

  const seenBoundHashes = (gate.boundObjects ?? []).map((bound) => bound.contentHash).filter((hash): hash is string => !!hash);

  const onApprove = () => {
    const warningDispositions = warnings.map((warning, index) => ({
      issueId: warning.issueId ?? undefined,
      disposition: dispositions[warningKey(warning, index)],
    }));
    const confirm = confirmationItems.map((item) => ({
      id: item.id,
      confirmed: !!confirmations[item.id],
    }));
    return approve.mutateAsync({
      gateId,
      body: { gateType: gate.type, seenBoundHashes, warningDispositions, confirmations: confirm },
    });
  };

  const onReject = () => {
    if (!rejectReason.trim()) return Promise.reject(new Error('reason required'));
    return reject.mutateAsync({ gateId, body: { gateType: gate.type, reason: rejectReason } });
  };

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="stage-toolbar">
        <div className="stage-toolbar__copy">
          <h2 className="stage-toolbar__title">
            {t('gate.title')} · {gateTypeLabel(gate.type)}
            <StatusBadge status={gate.status} />
          </h2>
          <p className="stage-toolbar__hint">{t('page.gate')}</p>
        </div>
        <div className="stage-toolbar__actions">
          <span className="chip">{t('gate.approvalProgress', { done: (gate.approvals ?? []).length, required: gate.requiredApprovals ?? 1 })}</span>
          {gate.environmentKey ? <span className="chip">{t('workspace.contextEnvironment')}: {gate.environmentKey}</span> : null}
        </div>
      </div>

      {gate.status === 'INVALIDATED' ? (
        <NoticeBar tone="error" title={t('gate.invalidatedTitle')}>
          <div className="stack">
            <span>{t('gate.invalidatedReason', { reason: gate.invalidatedReason ?? '—' })}</span>
            <span className="muted">{t('gate.invalidatedGuide')}</span>
          </div>
        </NoticeBar>
      ) : null}

      <div className="gate-layout">
        <div className="gate-main">
          <SectionCard title={t('gate.foundTitle')}>
            <ListOrDash items={foundItems} fallback={t('gate.noWarnings')} />
          </SectionCard>

          <SectionCard
            title={t('gate.warningsTitle')}
            description={warnings.length ? t('gate.warningMustHandle') : undefined}
          >
            {warnings.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>{t('gate.noWarnings')}</p>
            ) : (
              <div>
                {warnings.map((warning, index) => {
                  const key = warningKey(warning, index);
                  const disposition = dispositions[key];
                  return (
                    <div key={key} className="warning-row">
                      <div className="warning-row__body">
                        <span>
                          {warning.level ? <strong>[{severityLabel(warning.level)}] </strong> : null}
                          {warning.ruleId ? `${warning.ruleId}: ` : ''}
                          {warning.message}
                        </span>
                        {warning.sourceStage ? (
                          <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>
                            {t('workspace.failureFromStage')}: {stageTypeLabel(warning.sourceStage)}
                          </div>
                        ) : null}
                      </div>
                      <div className="warning-row__actions">
                        <Button secondary small toggled={disposition === 'ACCEPT'} onClick={() => setDispositions((previous) => ({ ...previous, [key]: 'ACCEPT' }))}>
                          {t('gate.warningAccept')}
                        </Button>
                        <Button secondary small toggled={disposition === 'FIX'} onClick={() => setDispositions((previous) => ({ ...previous, [key]: 'FIX' }))}>
                          {t('gate.warningNeedsFix')}
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </SectionCard>

          <SectionCard title={t('gate.willDoTitle')}>
            <p className="muted" style={{ margin: 0 }}>
              {t('gate.producesArtifacts')} · {seenBoundHashes.length}
            </p>
          </SectionCard>

          <SectionCard title={t('gate.rationaleTitle')}>
            {whyItems.length ? (
              <div className="stack">
                {whyItems.map((decision) => (
                  <DecisionCard key={decision.decisionId} decision={decision} projectId={projectId} />
                ))}
              </div>
            ) : (
              <p className="muted" style={{ margin: 0 }}>{t('gate.noRationale')}</p>
            )}
          </SectionCard>

          <SectionCard title={t('gate.uncertainTitle')}>
            <ListOrDash items={uncertainItems} fallback={t('gate.noWarnings')} />
          </SectionCard>

          <SectionCard title={t('gate.canChangeTitle')}>
            <ListOrDash items={canChangeItems} fallback={t('gate.noCanChange')} />
          </SectionCard>

          <SectionCard title={t('gate.willHappenTitle')}>
            <div className="stack" style={{ gap: 4 }}>
              {gate.environmentKey ? (
                <span>{t('gate.affectsEnvironment')}: <strong>{gate.environmentKey}</strong></span>
              ) : null}
              <span className="mono muted" style={{ fontSize: 12 }}>{t('gate.seenHashesNote', { count: seenBoundHashes.length })}</span>
            </div>
          </SectionCard>
        </div>

        <aside className="gate-side">
          <SectionCard title={t('gate.decisionPanel')} className="decision-card" tone={anyNeedsFix ? 'danger' : 'brand'}>
            <div className="decision-card__summary">
              <span>{t('gate.approvalProgress', { done: (gate.approvals ?? []).length, required: gate.requiredApprovals ?? 1 })}</span>
              <span>{t('gate.warningsRemaining', { count: warnings.length - Object.keys(dispositions).length })}</span>
            </div>
            {anyNeedsFix ? <NoticeBox warning>{t('gate.fixBlocksApprove')}</NoticeBox> : null}
            {!canApprove && !anyNeedsFix ? <p className="muted" style={{ margin: 0 }}>{t('gate.notReadyToApprove')}</p> : null}

            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.gateApprove}
              targetId={gateId}
              onExecute={onApprove}
              loading={approve.isPending}
              disabled={!canApprove}
            >
              {t('gate.approve')}
            </AllowedActionButton>

            <TextAreaInput
              label={t('gate.rejectReason')}
              value={rejectReason}
              onChange={setRejectReason}
              helpText={t('gate.rejectReasonHelp')}
            />
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.gateReject}
              targetId={gateId}
              tone="destructive"
              onExecute={onReject}
              loading={reject.isPending}
            >
              {t('gate.reject')}
            </AllowedActionButton>
          </SectionCard>

          {confirmationItems.length > 0 ? (
            <SectionCard title={t('gate.confirmationsTitle')}>
              <div className="stack" style={{ gap: 8 }}>
                {confirmationItems.map((item) => (
                  <CheckboxInput
                    key={item.id}
                    label={item.label ?? item.kind ?? item.id}
                    checked={!!confirmations[item.id]}
                    onChange={(value) => setConfirmations((previous) => ({ ...previous, [item.id]: value }))}
                  />
                ))}
              </div>
            </SectionCard>
          ) : null}

          {(gate.requiredApprovals ?? 1) > 1 || (gate.approvals ?? []).length > 0 ? (
            <SectionCard title={t('gate.approvalsTitle')}>
              <div className="stack" style={{ gap: 4 }}>
                <span>{t('gate.approvalProgress', { done: (gate.approvals ?? []).length, required: gate.requiredApprovals ?? 1 })}</span>
                {(gate.approvals ?? []).length === 0 ? <span className="muted">{t('gate.noApprovals')}</span> : null}
                {(gate.approvals ?? []).map((approval) => (
                  <span key={approval.id}>
                    <strong>{approval.approverName ?? '—'}</strong>
                    {' · '}
                    {approval.decision === 'APPROVED'
                      ? t('gate.decisionApproved')
                      : approval.decision === 'REJECTED'
                        ? t('gate.decisionRejected')
                        : statusLabel(approval.decision) || '—'}
                    {approval.createdAt ? <span className="muted"> · {approval.createdAt}</span> : null}
                  </span>
                ))}
              </div>
            </SectionCard>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

function ListOrDash({ items, fallback }: { items: string[]; fallback: string }) {
  if (!items.length) return <p className="muted" style={{ margin: 0 }}>{fallback}</p>;
  return (
    <ul className="issue-list">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  );
}
