/*
 * UI-AUTH-003 (FR-SEC-004): who belongs to this project, and in what role.
 *
 * Membership is replaced as a set, because S1's route is a PUT of the whole list: the screen keeps
 * a draft of the set and sends it in one intent, so a half-applied edit cannot leave someone with a
 * role and no membership row.  Availability comes from `member.manage` for *this* project —
 * the same evaluation the route will make, which is what lets a project's own member manager act
 * without holding the platform-wide right.
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
import { SelectInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { membersOptions, usersOptions, rolesOptions } from '@/api/queries';
import { useReplaceMembers, useRemoveMember } from '@/api/mutations';
import { useProjectWorkflow } from '@/hooks/useProjectWorkflow';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import type { MemberView, UserView } from '@/api/types';
import { roleLabel } from '@/lib/labels';

export function ProjectMembers() {
  const { t } = useTranslation();
  const { projectId = '' } = useParams();
  const { workflowId } = useProjectWorkflow(projectId);
  const members = useQuery(membersOptions(projectId));
  const users = useQuery(usersOptions());
  const roles = useQuery(rolesOptions());
  const replace = useReplaceMembers(projectId);
  const remove = useRemoveMember(projectId);
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState('');

  const rows = ((members.data as { items?: MemberView[] })?.items ?? []) as MemberView[];
  const people = ((users.data as { items?: UserView[] })?.items ?? []) as UserView[];
  const roleNames = ((roles.data as { items?: { name?: string }[] })?.items ?? []).map((item) => item.name ?? '');
  const failure = replace.error ?? remove.error;

  const add = async () => {
    const chosen = people.find((person) => person.id === userId);
    if (!chosen) return;
    await replace.mutateAsync({
      members: [
        ...rows.map((row) => ({ userId: row.userId, role: row.role, subject: row.subject })),
        { userId, role, subject: chosen.subject },
      ],
    });
    setUserId('');
    setRole('');
    void members.refetch();
  };

  return (
    <PageContainer title={t('admin.membersTitle')} description={t('page.members')} helpKey="manage">
      <div className="stack">
        <p className="muted">{t('admin.membersNote')}</p>
        {failure && isApiError(failure) ? (
          <NoticeBar tone="error" title={t('error.title')}>
            {translateErrorCode(failure.errorCode, failure.message)}
          </NoticeBar>
        ) : null}

        <Card>
          <Heading level={5} size="small">{t('admin.membersHeading')}</Heading>
          <AsyncShell
            loading={members.isLoading}
            error={members.error}
            empty={!rows.length}
            emptyTitle={t('admin.membersEmpty')}
            emptyHint={t('admin.membersEmptyHint')}
            onRetry={() => void members.refetch()}
          >
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  <DataTableCell tag="th">{t('admin.subject')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.role')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.actions')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {rows.map((row) => (
                  <DataTableRow key={`${row.userId}-${row.role ?? 'none'}`}>
                    <DataTableCell>
                      <span className="mono">{row.subject ?? row.userId}</span>
                    </DataTableCell>
                    <DataTableCell>
                      <Tag>{row.role ? roleLabel(row.role) : '—'}</Tag>
                    </DataTableCell>
                    <DataTableCell>
                      <AllowedActionButton
                        workflowId={workflowId}
                        // `member.manage` is answered per project by S1 (`project_actions()`), not just
                        // in the platform list: a role granted for one project would read as "may not
                        // manage it" if the page asked the platform answer, which has no project.
                        action={ACTION.projectMemberManage}
                        tone="secondary"
                        onExecute={async () => {
                          await remove.mutateAsync({ userId: String(row.userId) });
                          void members.refetch();
                        }}
                        loading={remove.isPending}
                      >
                        {t('admin.remove')}
                      </AllowedActionButton>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
          </AsyncShell>
        </Card>

        <Card>
          <Heading level={5} size="small">{t('admin.addMember')}</Heading>
          <div className="stack" style={{ gap: 8 }}>
            <SelectInput
              label={t('admin.subject')}
              value={userId}
              onChange={setUserId}
              options={people.map((person) => ({ label: String(person.subject), value: String(person.id) }))}
            />
            <SelectInput
              label={t('admin.role')}
              value={role}
              onChange={setRole}
              options={roleNames.map((name) => ({ label: roleLabel(name), value: name }))}
            />
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.projectMemberManage}
              onExecute={add}
              loading={replace.isPending}
              disabled={!userId || !role}
            >
              {t('admin.addMember')}
            </AllowedActionButton>
          </div>
        </Card>
      </div>
    </PageContainer>
  );
}
