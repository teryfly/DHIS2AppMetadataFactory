/*
 * UI-AUTH-003 (FR-SEC-001/002, CP-IAM-002): local accounts.
 *
 * Three things this screen must not do.  It does not infer anyone's rights from the role list it
 * can see — the controls here are enabled only by `user.manage` from `allowed-actions` (UI-BR-01).
 * It does not echo a password: the value goes into one request and is never held in state longer
 * than the submit (FR-SEC-007), which is why the reset form clears after a successful write.  And it
 * does not claim to delete an account, because S1 offers disable, not erase — an audit trail that
 * points at a removed row is worse than a disabled one.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag } from '@dhis2/ui';
import { PageContainer } from '@/lib/PageContainer';
import { RouteTabs } from '@/components/RouteTabs';
import { AsyncShell } from '@/components/AsyncShell';
import { Heading } from '@/components/typography';
import { NoticeBar } from '@/components/NoticeBar';
import { TextInput, SelectInput, CheckboxInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { PAGE_SIZE, usersOptions, rolesOptions, projectsListOptions } from '@/api/queries';
import { useCreateUser, useSetUserPassword, useAssignRole, useRevokeRole, usePatchUser } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import type { UserView } from '@/api/types';
import { identitySourceLabel, roleLabel, statusLabel } from '@/lib/labels';

export function UsersView() {
  const { t } = useTranslation();
  // S5IF-G04: the account list is paged by offset, so the screen says how many accounts S1 counted
  // and shows the page it fetched — an administration page that silently stops at 50 rows is how a
  // disabled account gets missed in an access review.
  const [offset, setOffset] = useState(0);
  const users = useQuery(usersOptions({ offset }));
  const roles = useQuery(rolesOptions());
  // The scope of a grant is chosen from the projects that exist, which is the same read the projects
  // screen makes; without it this panel can only offer organisation-wide roles.
  const projects = useQuery(projectsListOptions());
  const create = useCreateUser();
  const patch = usePatchUser();
  const [subject, setSubject] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [target, setTarget] = useState('');
  const [role, setRole] = useState('');
  const [grantProject, setGrantProject] = useState('');
  const [resetFor, setResetFor] = useState('');
  const [resetPassword, setResetPassword] = useState('');
  const [mustChange, setMustChange] = useState(true);
  const [editFor, setEditFor] = useState('');
  const [editName, setEditName] = useState('');
  const [editMail, setEditMail] = useState('');

  const rows = ((users.data as { items?: UserView[] })?.items ?? []) as UserView[];
  const totalUsers = (users.data as { total?: number })?.total ?? rows.length;
  const roleNames = ((roles.data as { items?: { name?: string }[] })?.items ?? []).map((item) => item.name ?? '');
  const assign = useAssignRole(target);
  const reset = useSetUserPassword(resetFor);
  const failure = create.error ?? assign.error ?? reset.error ?? patch.error;

  const byId = (userId: string) => rows.find((row) => row.id === userId);

  return (
    <PageContainer
      title={t('admin.usersTitle')}
      description={t('page.accounts')}
      helpKey="accounts"
      tabs={
        <RouteTabs
          label={t('nav.accounts')}
          tabs={[
            { key: 'users', to: '/users', label: t('admin.usersTitle'), active: true },
            { key: 'roles', to: '/roles', label: t('admin.rolesTitle'), active: false },
          ]}
        />
      }
    >
      <div className="stack">
        {failure && isApiError(failure) ? (
          <NoticeBar tone="error" title={t('error.title')}>
            <div className="stack" style={{ gap: 4 }}>
              <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
              {failure.correlationId ? (
                <span className="mono muted" style={{ fontSize: 12 }}>
                  {t('common.correlationId')}: {failure.correlationId}
                </span>
              ) : null}
            </div>
          </NoticeBar>
        ) : null}

        <div className="panel">
          <Heading level={5} size="small">{t('admin.usersHeading')}</Heading>
          {/* S1 has no delete endpoint, so retirement is the only exit — and that fact belonged on
              the screen, not only in the source comment. */}
          <p className="muted">{t('admin.noDeleteNote')}</p>
          <AsyncShell
            loading={users.isLoading}
            error={users.error}
            empty={!rows.length}
            emptyTitle={t('admin.usersEmpty')}
            emptyHint={t('admin.usersEmptyHint')}
            onRetry={() => void users.refetch()}
          >
            <div className="table-wrap">
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  <DataTableCell tag="th">{t('admin.subject')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.displayName')}</DataTableCell>
                  <DataTableCell tag="th">{t('common.status')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.mustChange')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.source')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.roles')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.actions')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {rows.map((row) => (
                  <DataTableRow key={String(row.id)}>
                    <DataTableCell>
                      <span className="mono">{row.subject}</span>
                    </DataTableCell>
                    <DataTableCell>{row.displayName || '—'}</DataTableCell>
                    <DataTableCell>
                      <Tag>{statusLabel(row.status)}</Tag>
                      {row.locked ? <Tag>{t('admin.locked')}</Tag> : null}
                    </DataTableCell>
                    <DataTableCell>{row.mustChangePassword ? t('common.yes') : t('common.no')}</DataTableCell>
                    <DataTableCell>{identitySourceLabel(row.source)}</DataTableCell>
                    <DataTableCell>
                      <HeldRoles user={row} />
                    </DataTableCell>
                    <DataTableCell>
                      <div className="table-actions">
                        <AllowedActionButton
                          workflowId={undefined}
                          scope="platform"
                          action={ACTION.userManage}
                          tone="secondary"
                          onExecute={() => {
                            setTarget(String(row.id));
                            setResetFor(String(row.id));
                          }}
                        >
                          {t('admin.resetPassword')}
                        </AllowedActionButton>
                        <AllowedActionButton
                          workflowId={undefined}
                          scope="platform"
                          action={ACTION.userManage}
                          tone="secondary"
                          onExecute={() => {
                            setEditFor(String(row.id));
                            setEditName(String(row.displayName ?? ''));
                            setEditMail(String(row.email ?? ''));
                          }}
                        >
                          {t('admin.editAccount')}
                        </AllowedActionButton>
                        <AllowedActionButton
                          workflowId={undefined}
                          scope="platform"
                          action={ACTION.userManage}
                          tone="secondary"
                          onExecute={() =>
                            row.status === 'ACTIVE'
                              ? patch.mutateAsync({ userId: String(row.id), body: { status: 'DISABLED' } }).then(() => users.refetch())
                              : patch.mutateAsync({ userId: String(row.id), body: { status: 'ACTIVE' } }).then(() => users.refetch())
                          }
                        >
                          {row.status === 'ACTIVE' ? t('admin.disable') : t('admin.enable')}
                        </AllowedActionButton>
                      </div>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
            </div>
            <div className="row" style={{ gap: 8, marginTop: 8, alignItems: 'center' }}>
              <span className="muted" style={{ fontSize: 12 }}>{t('admin.shownOf', { shown: rows.length, total: totalUsers })}</span>
              {offset > 0 ? (
                <Button secondary small onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}>
                  {t('common.previous')}
                </Button>
              ) : null}
              {rows.length === totalUsers ? null : (
                <Button secondary small onClick={() => setOffset((current) => current + PAGE_SIZE)}>
                  {t('common.next')}
                </Button>
              )}
            </div>
          </AsyncShell>
        </div>

        <div className="admin-form-grid">
        <div className="panel">
          <Heading level={5} size="small">{t('admin.createHeading')}</Heading>
          <div className="stack" style={{ gap: 8 }}>
            <TextInput label={t('admin.newSubject')} value={subject} onChange={setSubject} required />
            <TextInput label={t('admin.displayName')} value={displayName} onChange={setDisplayName} />
            <TextInput label={t('admin.email')} value={email} onChange={setEmail} type="email" />
            <TextInput
              label={t('admin.initialPassword')}
              value={password}
              onChange={setPassword}
              type="password"
            />
            <CheckboxInput
              label={t('admin.mustChange')}
              checked={mustChange}
              onChange={setMustChange}
              helpText={t('admin.mustChangeHelp')}
            />
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.userManage}
              onExecute={async () => {
                await create.mutateAsync({
                  subject: subject.trim(),
                  displayName: displayName.trim(),
                  email: email.trim(),
                  password,
                  // The default is true — an administrator is handing over an initial password — but
                  // it is now the operator's call rather than a constant baked into the screen. This
                  // is also how an account created with a known-good password ends up flagged, and
                  // the flag is the one thing this console could not clear. CP-IAM-005 ③ keeps it
                  // report-only either way.
                  mustChangePassword: mustChange,
                });
                setPassword('');
                setSubject('');
                setDisplayName('');
                setEmail('');
                setMustChange(true);
                void users.refetch();
              }}
              loading={create.isPending}
              disabled={!subject.trim() || !password}
            >
              {t('admin.create')}
            </AllowedActionButton>
          </div>
        </div>

        {/* The two facts that need correcting after an account exists.  The login name is not among
            them on purpose: it is the string carried in the audit trail and in approval records, so
            editing it would rewrite history rather than fix a label — S1 does not offer it either. */}
        {editFor ? (
          <div className="panel">
            <Heading level={5} size="small">
              {t('admin.editFor', { subject: byId(editFor)?.subject ?? editFor })}
            </Heading>
            <div className="stack" style={{ gap: 8 }}>
              <TextInput label={t('admin.displayName')} value={editName} onChange={setEditName} helpText={t('admin.editNameHelp')} />
              <TextInput label={t('admin.email')} value={editMail} onChange={setEditMail} type="email" />
              <AllowedActionButton
                workflowId={undefined}
                scope="platform"
                action={ACTION.userManage}
                onExecute={async () => {
                  await patch.mutateAsync({ userId: editFor, body: { displayName: editName.trim(), email: editMail.trim() } });
                  setEditFor('');
                  setEditName('');
                  setEditMail('');
                  void users.refetch();
                }}
                loading={patch.isPending}
              >
                {t('admin.saveAccount')}
              </AllowedActionButton>
              <AllowedActionButton
                workflowId={undefined}
                scope="platform"
                action={ACTION.userManage}
                tone="secondary"
                onExecute={() => {
                  setEditFor('');
                  setEditName('');
                  setEditMail('');
                }}
              >
                {t('common.cancel')}
              </AllowedActionButton>
            </div>
          </div>
        ) : null}

        {resetFor ? (
          <div className="panel">
            <Heading level={5} size="small">
              {t('admin.resetFor', { subject: byId(resetFor)?.subject ?? resetFor })}
            </Heading>
            <div className="stack" style={{ gap: 8 }}>
              <TextInput label={t('admin.newPassword')} value={resetPassword} onChange={setResetPassword} type="password" />
              <AllowedActionButton
                workflowId={undefined}
                scope="platform"
                action={ACTION.userManage}
                onExecute={async () => {
                  await reset.mutateAsync({ password: resetPassword, mustChangePassword: true });
                  setResetPassword('');
                  setResetFor('');
                }}
                loading={reset.isPending}
                disabled={resetPassword.length < 1}
              >
                {t('admin.resetPassword')}
              </AllowedActionButton>
            </div>
          </div>
        ) : null}

        <div className="panel">
          <Heading level={5} size="small">{t('admin.roleHeading')}</Heading>
          <div className="stack" style={{ gap: 8 }}>
            <SelectInput
              label={t('admin.subject')}
              value={target}
              onChange={setTarget}
              options={rows.map((row) => ({ label: String(row.subject), value: String(row.id) }))}
            />
            <SelectInput
              label={t('admin.role')}
              value={role}
              onChange={setRole}
              options={roleNames.map((name) => ({ label: roleLabel(name), value: name }))}
            />
            {/* Scope is picked from the projects that exist (CP-IAM-002). A role with a project id is a
                 grant *inside that project*: the table reads those back as `ROLE · project` and the
                 revoke names the project, so the console has to be able to make one as well. Free text
                 could only ever be copied in from another screen or fail with a 404. */}
            <SelectInput
              label={t('admin.grantProject')}
              value={grantProject}
              onChange={setGrantProject}
              helpText={t('admin.grantProjectHelp')}
              options={[
                { label: t('admin.orgWide'), value: '' },
                ...(((projects.data as { items?: { id?: string; name?: string }[] })?.items ?? [])
                  .map((p) => ({ label: String(p.name || p.id), value: String(p.id) }))),
              ]}
            />
            <div className="row" style={{ gap: 8 }}>
              <AllowedActionButton
                workflowId={undefined}
                scope="platform"
                action={ACTION.userManage}
                onExecute={async () => {
                  await assign.mutateAsync({ role, projectId: grantProject.trim() || undefined });
                  setGrantProject('');
                  void users.refetch();
                }}
                loading={assign.isPending}
                disabled={!target || !role}
              >
                {t('admin.assignRole')}
              </AllowedActionButton>
            </div>
          </div>
        </div>
        </div>
      </div>
    </PageContainer>
  );
}

/**
 * What the account actually holds, each row revocable on its own.  Revoking from a dropdown of the
 * built-in roles could name a role the person never had, and S1's answer to that is a silent no-op
 * or a 404 the operator reads as "the button is broken" — so the list, not a guess, is the target.
 */
function HeldRoles({ user }: { user: UserView }) {
  const { t } = useTranslation();
  const revoke = useRevokeRole(String(user.id));
  const held = user.roles ?? [];
  if (!held.length) return <span className="muted">{t('admin.noRoles')}</span>;
  return (
    <div className="stack" style={{ gap: 4 }}>
      {held.map((row) => (
        <div className="row" key={`${row.role}:${row.projectId ?? ''}`} style={{ gap: 6 }}>
          <Tag>{row.projectId ? `${roleLabel(row.role)} · ${row.projectId}` : roleLabel(row.role)}</Tag>
          <AllowedActionButton
            workflowId={undefined}
            scope="platform"
            action={ACTION.userManage}
            tone="secondary"
            loading={revoke.isPending}
            onExecute={() => revoke.mutateAsync({ role: row.role, projectId: row.projectId ?? undefined })}
          >
            {t('admin.revoke')}
          </AllowedActionButton>
        </div>
      ))}
    </div>
  );
}
