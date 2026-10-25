/*
 * UI-ADM-001: one environment as a page rather than a row. Two things this console could not do
 * before: read `GET /environments/{key}` and `GET .../capability-profile` at all (the query key for
 * the profile had been sitting in `queryKeys.ts` unused), and show the protection settings in the
 * order an operator decides in — what the environment is, what it currently allows, what the probe
 * said and when.
 */
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { NoticeBar } from '@/components/NoticeBar';
import { EmptyState } from '@/components/EmptyState';
import { Heading } from '@/components/typography';
import { capabilityProfileOptions, environmentOptions, environmentProtectionOptions } from '@/api/queries';
import { authModeLabel, dataPolicyLabel, deletionPolicyLabel, envClassLabel, freshnessLabel, roleLabel, statusLabel } from '@/lib/labels';

function Field({ label, value }: { label: string; value: unknown }) {
  const { t } = useTranslation();
  const shown =
    value === null || value === undefined || value === ''
      ? '—'
      : typeof value === 'boolean'
        ? value
          ? t('common.yes')
          : t('common.no')
        : String(value);
  return (
    <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
      <span className="muted" style={{ minWidth: 200 }}>{label}</span>
      <span className="mono">{shown}</span>
    </div>
  );
}

/** Everything the probe returned that the page has no dedicated row for, as a readable table.
 *  Not a JSON dump: an operator comparing two environments reads labels, not brackets. */
function RestTable({ rows }: { rows: Record<string, unknown> }) {
  const { t } = useTranslation();
  const entries = Object.entries(rows);
  if (entries.length === 0) return <p className="muted">{t('environmentDetail.noExtra')}</p>;
  return (
    <DataTable>
      <DataTableHead>
        <DataTableRow>
          <DataTableCell tag="th">{t('environmentDetail.field')}</DataTableCell>
          <DataTableCell tag="th">{t('environmentDetail.value')}</DataTableCell>
        </DataTableRow>
      </DataTableHead>
      <DataTableBody>
        {entries.map(([key, value]) => (
          <DataTableRow key={key}>
            <DataTableCell><span className="mono">{key}</span></DataTableCell>
            <DataTableCell>
              {typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}
            </DataTableCell>
          </DataTableRow>
        ))}
      </DataTableBody>
    </DataTable>
  );
}

export function EnvironmentDetailView() {
  const { t } = useTranslation();
  const { environmentKey = '' } = useParams();
  const environment = useQuery(environmentOptions(environmentKey || undefined));
  const protection = useQuery(environmentProtectionOptions(environmentKey));
  const profile = useQuery(capabilityProfileOptions(environmentKey || undefined));

  if (environment.isLoading) return <Loading />;

  const failed = environment.isError || protection.isError || profile.isError;
  const env = environment.data;

  return (
    <PageContainer
      title={env?.name || environmentKey}
      description={t('page.environments')}
      helpKey="environments"
      actions={
        <Link to="/environments" className="secondary-link">{t('environmentDetail.backToList')}</Link>
      }
    >
      <div className="stack">
        {failed ? (
          <NoticeBar tone="error" title={t('error.title')}>
            {t('environmentDetail.partialFailure')}
          </NoticeBar>
        ) : null}

        {!env ? (
          <EmptyState>
            <p>{t('environmentDetail.noneTitle')}</p>
            <p className="muted">{t('environmentDetail.noneBody')}</p>
            <Link to="/environments">{t('environmentDetail.backToList')}</Link>
          </EmptyState>
        ) : (
          <Card>
            <div className="stack" style={{ gap: 4 }}>
              <Heading level={5} size="small">{t('environmentDetail.identity')}</Heading>
              <Field label={t('environments.key')} value={env.environmentKey} />
              <Field label={t('environments.class')} value={envClassLabel(env.environmentClass)} />
              <Field label={t('environments.baseUrl')} value={env.baseUrl} />
              <Field label={t('environments.version')} value={env.dhis2Version ?? env.versionConstraint} />
              <Field label={t('common.status')} value={statusLabel(env.status)} />
              <Field label={t('environments.authMode')} value={authModeLabel(env.authMode)} />
              <Field label={t('environments.timeout')} value={env.timeoutSeconds} />
              <Field label={t('environments.tlsRequired')} value={env.tlsRequired} />
              {env.notes ? <p className="muted">{String(env.notes)}</p> : null}
            </div>
          </Card>
        )}

        <Card>
          <div className="stack" style={{ gap: 4 }}>
            <Heading level={5} size="small">{t('environmentDetail.protection')}</Heading>
            <Field label={t('environments.timezone')} value={protection.data?.timezone} />
            <Field label={t('environments.withinWindow')} value={protection.data?.withinCommitWindow} />
            <Field label={t('environments.deletionPolicy')} value={deletionPolicyLabel(protection.data?.deletionPolicy)} />
            <Field label={t('environmentDetail.requiredApprovals')} value={protection.data?.requiredApprovals} />
            <Field label={t('environmentDetail.operatorRole')} value={roleLabel(protection.data?.operatorRole)} />
            <Field label={t('environmentDetail.fixtureImport')} value={protection.data?.allowsFixtureImport} />
            <Field label={t('environmentDetail.dataExportPolicy')} value={protection.data?.dataExportPolicy} />
            <Field label={t('environmentDetail.llmDataPolicy')} value={dataPolicyLabel(protection.data?.llmDataPolicy)} />
            <Field label={t('environments.emergencyLock')} value={protection.data?.emergencyLocked} />
            {protection.data?.emergencyLocked ? (
              <>
                <Field label={t('environmentDetail.lockReason')} value={protection.data.lockReason} />
                <Field label={t('environmentDetail.lockChangedBy')} value={protection.data.lockChangedBy} />
                <Field label={t('environmentDetail.lockChangedAt')} value={protection.data.lockChangedAt} />
              </>
            ) : null}
            {(protection.data?.commitWindows ?? []).length === 0 ? (
              <p className="muted">{t('environments.noWindows')}</p>
            ) : (
              <DataTable>
                <DataTableHead>
                  <DataTableRow>
                    <DataTableCell tag="th">{t('environments.windowDay')}</DataTableCell>
                    <DataTableCell tag="th">{t('environments.windowRange')}</DataTableCell>
                  </DataTableRow>
                </DataTableHead>
                <DataTableBody>
                  {(protection.data?.commitWindows ?? []).map((w, i) => (
                    <DataTableRow key={i}>
                      <DataTableCell>{String((w as { day?: string }).day ?? '—')}</DataTableCell>
                      <DataTableCell>{`${String((w as { from?: string }).from ?? '—')} – ${String((w as { to?: string }).to ?? '—')}`}</DataTableCell>
                    </DataTableRow>
                  ))}
                </DataTableBody>
              </DataTable>
            )}
          </div>
        </Card>

        <Card>
          <div className="stack" style={{ gap: 6 }}>
            <Heading level={5} size="small">{t('environmentDetail.capability')}</Heading>
            {!profile.data ? (
              <EmptyState>
                <p>{t('environmentDetail.noProfileTitle')}</p>
                <p className="muted">{t('environmentDetail.noProfileBody')}</p>
              </EmptyState>
            ) : (
              <>
                <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
                  <Field label={t('environmentDetail.profileVersion')} value={profile.data.profileVersion} />
                  <Tag>{profile.data.dhis2Version ?? '—'}</Tag>
                  {/* the word carries the verdict; colour is decoration, not the message */}
                  <Tag neutral>{profile.data.stale ? t('environmentDetail.stale') : t('environmentDetail.fresh')}</Tag>
                </div>
                <Field label={t('environments.probedAt')} value={profile.data.probedAt} />
                <Field label={t('environmentDetail.freshness')} value={freshnessLabel(profile.data.freshness)} />
                {profile.data.staleReason ? <p className="muted">{String(profile.data.staleReason)}</p> : null}
                <RestTable rows={(profile.data.content ?? {}) as Record<string, unknown>} />
              </>
            )}
          </div>
        </Card>
      </div>
    </PageContainer>
  );
}
