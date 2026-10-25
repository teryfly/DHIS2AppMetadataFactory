/*
 * UI-AUTH-003 (FR-SEC-003): authorising a person to read PHI_PII / RESTRICTED artifacts.
 *
 * The reason is part of the record, not a courtesy: the requirement says a restricted read must be
 * separately authorised *and* audited, so the form cannot submit without one.  Nothing here shows a
 * credential or a masked value (S5IF-G09) — a grant names a person, a classification and an expiry.
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
import { SelectInput, TextInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { artifactGrantsOptions, usersOptions } from '@/api/queries';
import { useAddGrant, useRevokeGrant } from '@/api/mutations';
import { useProjectWorkflow } from '@/hooks/useProjectWorkflow';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import type { GrantView, UserView } from '@/api/types';
import { classificationLabel } from '@/lib/labels';

export function ArtifactGrants() {
  const { t } = useTranslation();
  const { projectId = '' } = useParams();
  const { workflowId } = useProjectWorkflow(projectId);
  const grants = useQuery(artifactGrantsOptions(projectId));
  const users = useQuery(usersOptions());
  const add = useAddGrant(projectId);
  const revoke = useRevokeGrant(projectId);
  const [userId, setUserId] = useState('');
  const [classification, setClassification] = useState('PHI_PII');
  const [reason, setReason] = useState('');
  const [expiresAt, setExpiresAt] = useState('');

  const rows = ((grants.data as { items?: GrantView[] })?.items ?? []) as GrantView[];
  const people = ((users.data as { items?: UserView[] })?.items ?? []) as UserView[];
  const failure = add.error ?? revoke.error;

  return (
    <PageContainer title={t('admin.grantsTitle')} description={t('page.grants')} helpKey="manage">
      <div className="stack">
        <p className="muted">{t('admin.grantsNote')}</p>
        {failure && isApiError(failure) ? (
          <NoticeBar tone="error" title={t('error.title')}>
            {translateErrorCode(failure.errorCode, failure.message)}
          </NoticeBar>
        ) : null}

        <Card>
          <Heading level={5} size="small">{t('admin.grantsHeading')}</Heading>
          <AsyncShell
            loading={grants.isLoading}
            error={grants.error}
            empty={!rows.length}
            emptyTitle={t('admin.grantsEmpty')}
            emptyHint={t('admin.grantsEmptyHint')}
            onRetry={() => void grants.refetch()}
          >
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  <DataTableCell tag="th">{t('admin.subject')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.classification')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.reason')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.expiresAt')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.actions')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {rows.map((row) => (
                  <DataTableRow key={`${row.userId}-${row.classification}`}>
                    <DataTableCell>
                      <span className="mono">{row.subject ?? row.userId}</span>
                    </DataTableCell>
                    <DataTableCell>
                      <Tag>{classificationLabel(row.classification)}</Tag>
                    </DataTableCell>
                    <DataTableCell>{row.reason || '—'}</DataTableCell>
                    <DataTableCell>{row.expiresAt ?? t('admin.noExpiry')}</DataTableCell>
                    <DataTableCell>
                      <AllowedActionButton
                        workflowId={workflowId}
                        // answered per project by S1 (`project_actions()`), the same evaluation the
                        // route will make — the platform answer has no project to filter on
                        action={ACTION.artifactGrantManage}
                        tone="secondary"
                        onExecute={async () => {
                          await revoke.mutateAsync({
                            userId: String(row.userId),
                            classification: String(row.classification),
                          });
                          void grants.refetch();
                        }}
                        loading={revoke.isPending}
                      >
                        {t('admin.revoke')}
                      </AllowedActionButton>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
          </AsyncShell>
        </Card>

        <Card>
          <Heading level={5} size="small">{t('admin.grantAdd')}</Heading>
          <div className="stack" style={{ gap: 8 }}>
            <SelectInput
              label={t('admin.subject')}
              value={userId}
              onChange={setUserId}
              options={people.map((person) => ({ label: String(person.subject), value: String(person.id) }))}
            />
            <SelectInput
              label={t('admin.classification')}
              value={classification}
              onChange={setClassification}
              options={[
                { label: classificationLabel('PHI_PII'), value: 'PHI_PII' },
                { label: classificationLabel('RESTRICTED'), value: 'RESTRICTED' },
              ]}
            />
            <TextInput label={t('admin.reason')} value={reason} onChange={setReason} required />
            <TextInput
              label={t('admin.expiresAt')}
              value={expiresAt}
              onChange={setExpiresAt}
              type="datetime-local"
              helpText={t('admin.expiresHelp')}
            />
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.artifactGrantManage}
              onExecute={async () => {
                await add.mutateAsync({
                  userId,
                  classification,
                  reason: reason.trim(),
                  expiresAt: expiresAt ? new Date(expiresAt).toISOString() : null,
                });
                setReason('');
                setExpiresAt('');
                void grants.refetch();
              }}
              loading={add.isPending}
              disabled={!userId || !reason.trim()}
            >
              {t('admin.grantAdd')}
            </AllowedActionButton>
          </div>
        </Card>
      </div>
    </PageContainer>
  );
}
