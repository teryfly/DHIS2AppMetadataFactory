/*
 * UI-AUTH-003 (CP-WF-020, CP-IAM-002): production exemptions — the recorded waiver that lets a
 * delivery proceed in production outside the normal protection, with a reason and an expiry.
 *
 * Revocation is one DELETE whose target is named in the path, so no idempotency key is sent; the
 * grant carries one, because a retried POST that minted a second exemption would be a second waiver
 * on the record.
 */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag } from '@dhis2/ui';
import { PageContainer } from '@/lib/PageContainer';
import { AsyncShell } from '@/components/AsyncShell';
import { Heading } from '@/components/typography';
import { NoticeBar } from '@/components/NoticeBar';
import { TextInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { productionExemptionsOptions } from '@/api/queries';
import { useAddExemption, useRevokeExemption } from '@/api/mutations';
import { useProjectWorkflow } from '@/hooks/useProjectWorkflow';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import type { ExemptionView } from '@/api/types';

export function ProductionExemptions() {
  const { t } = useTranslation();
  const { projectId = '' } = useParams();
  const { workflowId } = useProjectWorkflow(projectId);
  const exemptions = useQuery(productionExemptionsOptions(projectId));
  const add = useAddExemption(projectId);
  const revoke = useRevokeExemption(projectId);
  const [reason, setReason] = useState('');
  const [waiver, setWaiver] = useState('');
  const [environmentKey, setEnvironmentKey] = useState('');
  const [expiresAt, setExpiresAt] = useState('');

  const rows = ((exemptions.data as { items?: ExemptionView[] })?.items ?? []) as ExemptionView[];
  const failure = add.error ?? revoke.error;

  return (
    <PageContainer title={t('admin.exemptionsTitle')} description={t('page.exemptions')} helpKey="manage">
      <div className="stack">
        <p className="muted">{t('admin.exemptionsNote')}</p>
        {failure && isApiError(failure) ? (
          <NoticeBar tone="error" title={t('error.title')}>
            {translateErrorCode(failure.errorCode, failure.message)}
          </NoticeBar>
        ) : null}

        <Card>
          <Heading level={5} size="small">{t('admin.exemptionsHeading')}</Heading>
          <AsyncShell
            loading={exemptions.isLoading}
            error={exemptions.error}
            empty={!rows.length}
            emptyTitle={t('admin.exemptionsEmpty')}
            emptyHint={t('admin.exemptionsEmptyHint')}
            onRetry={() => void exemptions.refetch()}
          >
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  <DataTableCell tag="th">{t('admin.waiver')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.environment')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.reason')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.expiresAt')}</DataTableCell>
                  <DataTableCell tag="th">{t('common.status')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.actions')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {rows.map((row) => (
                  <DataTableRow key={String(row.id)}>
                    <DataTableCell>
                      <span className="mono">{row.waiver ?? '—'}</span>
                    </DataTableCell>
                    <DataTableCell>{row.environmentKey ?? '—'}</DataTableCell>
                    <DataTableCell>{row.reason || '—'}</DataTableCell>
                    <DataTableCell>{row.expiresAt ?? t('admin.noExpiry')}</DataTableCell>
                    <DataTableCell>
                      <Tag>{row.revoked ? t('admin.revoked') : t('admin.active')}</Tag>
                    </DataTableCell>
                    <DataTableCell>
                      {row.revoked ? (
                        <span className="muted">—</span>
                      ) : (
                        <AllowedActionButton
                          workflowId={workflowId}
                          // answered per project by S1 (`project_actions()`), the same evaluation the
                          // route will make — the platform answer has no project to filter on
                          action={ACTION.productionExemptionManage}
                          tone="secondary"
                          onExecute={async () => {
                            await revoke.mutateAsync({ exemptionId: String(row.id) });
                            void exemptions.refetch();
                          }}
                          loading={revoke.isPending}
                        >
                          {t('admin.revoke')}
                        </AllowedActionButton>
                      )}
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
          </AsyncShell>
        </Card>

        <Card>
          <Heading level={5} size="small">{t('admin.exemptionAdd')}</Heading>
          <div className="stack" style={{ gap: 8 }}>
            <TextInput label={t('admin.waiver')} value={waiver} onChange={setWaiver} helpText={t('admin.waiverHelp')} />
            <TextInput label={t('admin.environment')} value={environmentKey} onChange={setEnvironmentKey} />
            <TextInput label={t('admin.reason')} value={reason} onChange={setReason} required />
            <TextInput label={t('admin.expiresAt')} value={expiresAt} onChange={setExpiresAt} type="datetime-local" />
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.productionExemptionManage}
              onExecute={async () => {
                await add.mutateAsync({
                  reason: reason.trim(),
                  waiver: waiver.trim() || null,
                  environmentKey: environmentKey.trim() || null,
                  expiresAt: expiresAt ? new Date(expiresAt).toISOString() : null,
                });
                setReason('');
                setWaiver('');
                void exemptions.refetch();
              }}
              loading={add.isPending}
              disabled={!reason.trim()}
            >
              {t('admin.exemptionAdd')}
            </AllowedActionButton>
          </div>
        </Card>
      </div>
    </PageContainer>
  );
}
