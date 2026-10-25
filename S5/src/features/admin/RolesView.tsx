/*
 * UI-AUTH-003 (FR-SEC-002): the role table, as S1 defines it.
 *
 * Read-only by design: the roles are built into S1 (`identity/roles.py`) and the contract offers no
 * role editor, so inventing one would put a permission the deployment cannot grant on screen. What
 * this page does answer is the question an administrator actually asks — which action does this
 * role let someone perform.
 */
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag } from '@dhis2/ui';
import { PageContainer } from '@/lib/PageContainer';
import { RouteTabs } from '@/components/RouteTabs';
import { AsyncShell } from '@/components/AsyncShell';
import { Heading } from '@/components/typography';
import { rolesOptions } from '@/api/queries';
import { roleLabel } from '@/lib/labels';

type RoleRow = { name?: string; actions?: string[] };

export function RolesView() {
  const { t } = useTranslation();
  const roles = useQuery(rolesOptions());
  const rows = ((roles.data as { items?: RoleRow[] })?.items ?? []) as RoleRow[];

  return (
    <PageContainer
      title={t('admin.rolesTitle')}
      description={t('page.roles')}
      helpKey="accounts"
      tabs={
        <RouteTabs
          label={t('nav.accounts')}
          tabs={[
            { key: 'users', to: '/users', label: t('admin.usersTitle'), active: false },
            { key: 'roles', to: '/roles', label: t('admin.rolesTitle'), active: true },
          ]}
        />
      }
    >
      <div className="stack">
        <p className="muted">{t('admin.rolesNote')}</p>
        {/* The roles are S1's own table, so an empty answer is a server that refused to speak, not a
            factory with no permissions — which is why the shell's error arm is the one to trust. */}
        <AsyncShell
          loading={roles.isLoading}
          error={roles.error}
          empty={!rows.length}
          emptyTitle={t('admin.rolesEmpty')}
          onRetry={() => void roles.refetch()}
        >
          <div className="panel">
            <Heading level={5} size="small">{t('admin.rolesHeading')}</Heading>
            <div className="table-wrap">
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  <DataTableCell tag="th">{t('admin.role')}</DataTableCell>
                  <DataTableCell tag="th">{t('admin.roleActions')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {rows.map((row) => (
                  <DataTableRow key={String(row.name)}>
                    <DataTableCell>
                      <strong>{roleLabel(row.name)}</strong>
                    </DataTableCell>
                    <DataTableCell>
                      <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                        {(row.actions ?? []).map((action) => (
                          <Tag key={action}>{action}</Tag>
                        ))}
                      </div>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
            </div>
          </div>
        </AsyncShell>
      </div>
    </PageContainer>
  );
}
