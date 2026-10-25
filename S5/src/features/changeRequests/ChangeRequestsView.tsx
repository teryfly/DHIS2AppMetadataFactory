/*
 * UI-OPS-001: change requests — create (reason + affected entries), impact report, and the
 * approve / reject / start / complete / cancel steps.
 *
 * Every button here is gated by allowed-actions with the change request as its target: which step
 * a request may take next is CP-CR-002's state machine, which S1 owns and S5 must not re-derive
 * from the status string it happens to have on screen (FR-UI-009, UI-BR-01).
 */
import { useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, DataTable, DataTableBody, DataTableCell, DataTableRow, Modal, ModalActions, ModalContent, ModalTitle, Tag } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';
import { StatusBadge } from '@/components/StatusBadge';
import { stageTypeLabel, statusLabel } from '@/lib/labels';
import { NoticeBar } from '@/components/NoticeBar';
import { TextAreaInput, TextInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { actionIntent } from '@/api/idempotency';
import { changeRequestsOptions } from '@/api/queries';
import { useChangeRequestStep, useCreateChangeRequest, type ChangeRequestStep } from '@/api/mutations';
import type { ChangeRequestView } from '@/api/types';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';

export function ChangeRequestsView() {
  const { t } = useTranslation();
  const ctx = useOutletContext<WorkspaceContext>();
  const crs = useQuery(changeRequestsOptions(ctx.projectId));
  const [creating, setCreating] = useState(false);
  const [reason, setReason] = useState('');
  const [keys, setKeys] = useState('');
  const [retiring, setRetiring] = useState('');
  const create = useCreateChangeRequest(ctx.projectId);
  const items = crs.data?.items ?? [];

  const splitKeys = (value: string) => value.split(',').map((key) => key.trim()).filter(Boolean);

  const submitCreate = () =>
    create.mutateAsync({
      body: {
        reason,
        affectedBusinessKeys: splitKeys(keys),
        // CP-CR-005: a directed deletion is authorized by the request itself. S2's impact read has
        // no deletion field to fill it in, so an entry named here is the only way it ever gets
        // retired — and S1 refuses the request outright if the entry is not DEPRECATED.
        deletionCandidates: splitKeys(retiring),
      },
    }).then(() => {
      setReason('');
      setKeys('');
      setRetiring('');
      setCreating(false);
    });

  return (
    <PageContainer
      title={t('changeRequests.title')}
      description={t('page.changeRequests')}
      helpKey="delivery"
      actions={
        <AllowedActionButton
          workflowId={ctx.workflowId}
          action={ACTION.changeRequestCreate}
          onExecute={() => setCreating(true)}
        >
          {t('changeRequests.create')}
        </AllowedActionButton>
      }
    >
      {crs.isLoading ? <Loading /> : null}
      {!crs.isLoading && !items.length ? <EmptyState>{t('common.notFound')}</EmptyState> : null}
      <div className="stack">
        {items.map((cr) => (
          <ChangeRequestCard key={cr.id} cr={cr} workflowId={ctx.workflowId} />
        ))}
      </div>

      {creating ? (
        <Modal onClose={() => setCreating(false)}>
          <ModalTitle>{t('changeRequests.create')}</ModalTitle>
          <ModalContent>
            <div className="stack">
              <TextAreaInput label={t('changeRequests.reason')} value={reason} onChange={setReason} required />
              <TextInput
                label={t('changeRequests.affectedKeys')}
                value={keys}
                onChange={setKeys}
                helpText={t('changeRequests.affectedKeysHint')}
              />
              <TextInput
                label={t('changeRequests.retireKeys')}
                value={retiring}
                onChange={setRetiring}
                helpText={t('changeRequests.retireKeysHint')}
              />
              {create.error ? <NoticeBar tone="error">{(create.error as Error).message}</NoticeBar> : null}
            </div>
          </ModalContent>
          <ModalActions>
            <Button secondary onClick={() => setCreating(false)}>{t('common.cancel')}</Button>
            <Button primary disabled={!reason.trim() || create.isPending} loading={create.isPending} onClick={submitCreate}>
              {t('common.confirm')}
            </Button>
          </ModalActions>
        </Modal>
      ) : null}
    </PageContainer>
  );
}

function ChangeRequestCard({ cr, workflowId }: { cr: ChangeRequestView; workflowId?: string }) {
  const { t } = useTranslation();
  const step = useChangeRequestStep(cr.projectId ?? '');
  const [impact, setImpact] = useState<Record<string, unknown> | null>(null);

  const run = (name: ChangeRequestStep) =>
    step.mutateAsync({ crId: cr.id, step: name, intent: actionIntent('POST', `/change-requests/${name}`, cr.id) });

  return (
    <Card>
      <div className="stack" style={{ gap: 8 }}>
        <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
          <div className="stack">
            <strong>{cr.reason ?? cr.id}</strong>
            <span className="muted" style={{ fontSize: 12 }}>
              {cr.id} · {cr.requestedBy ?? '—'} · {statusLabel(cr.status)}
            </span>
          </div>
          <StatusBadge status={cr.status ?? 'DRAFT'} />
        </div>

        {cr.affectedBusinessKeys?.length ? (
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            {t('changeRequests.affected')}: <span className="mono">{cr.affectedBusinessKeys.join(', ')}</span>
          </p>
        ) : null}

        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          {/* One targetId per request: two open requests in one project must not share an answer. */}
          <AllowedActionButton
            workflowId={workflowId}
            action={ACTION.changeRequestImpact}
            targetId={cr.id}
            tone="secondary"
            onExecute={() => run('impact').then((answer) => setImpact(answer))}
          >
            {t('changeRequests.impact')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action={ACTION.changeRequestApprove} targetId={cr.id} tone="secondary" onExecute={() => run('approve')}>
            {t('changeRequests.approve')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action={ACTION.changeRequestReject} targetId={cr.id} tone="destructive" onExecute={() => run('reject')}>
            {t('changeRequests.reject')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action={ACTION.changeRequestStart} targetId={cr.id} tone="secondary" onExecute={() => run('start')}>
            {t('changeRequests.start')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action={ACTION.changeRequestComplete} targetId={cr.id} tone="secondary" onExecute={() => run('complete')}>
            {t('changeRequests.complete')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action={ACTION.changeRequestCancel} targetId={cr.id} tone="destructive" onExecute={() => run('cancel')}>
            {t('changeRequests.cancel')}
          </AllowedActionButton>
        </div>

        {impact ? <ImpactReport impact={impact} /> : null}
        {step.error ? <NoticeBar tone="error">{(step.error as Error).message}</NoticeBar> : null}
      </div>
    </Card>
  );
}

function ImpactReport({ impact }: { impact: Record<string, unknown> }) {
  const { t } = useTranslation();
  const list = (key: string) => (Array.isArray(impact[key]) ? (impact[key] as unknown[]).map(String) : []);
  const entries: [string, string[]][] = [
    [t('changeRequests.affectedStages'), list('affectedStages').map(stageTypeLabel)],
    [t('changeRequests.affectedObjects'), list('affectedObjectKeys')],
    [t('changeRequests.affectedPages'), list('affectedPages')],
    [t('changeRequests.deletionCandidates'), list('deletionCandidates')],
  ];
  const rows = entries.filter(([, values]) => values.length > 0);

  if (!rows.length) return <p className="muted" style={{ fontSize: 12 }}>{t('changeRequests.noImpact')}</p>;

  return (
    <div className="stack" style={{ gap: 4 }}>
      <Tag>{t('changeRequests.impact')}</Tag>
      <DataTable>
        <DataTableBody>
          {rows.map(([label, values]) => (
            <DataTableRow key={label}>
              <DataTableCell>{label}</DataTableCell>
              <DataTableCell className="mono">{values.join(', ')}</DataTableCell>
            </DataTableRow>
          ))}
        </DataTableBody>
      </DataTable>
    </div>
  );
}
