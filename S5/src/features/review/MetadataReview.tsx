/*
 * UI-REV-003 / FR-UI-007: the metadata review. Registry view and by-object-type view, the
 * create/update/delete statistics, the current-vs-compiled diff, and the capability compatibility
 * warnings.
 *
 * The statistics and the warnings come from the deployment run's own summary (S1 assembles them
 * from S2's diff and S4's capability profile). Counting registry rows here would produce numbers
 * that look the same and mean something different: "what this project manages" is not "what this
 * run will change in the environment".
 *
 * The by-file view reads S2's build record through S1 (S5IF-BP17): which files a build produced,
 * their hashes and how many objects each carries. The grouping is not parsed in the browser from
 * the package bytes — the file list is S2's own answer, so the view cannot drift from what was
 * actually compiled.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useOutletContext, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, DataTable, DataTableBody, DataTableCell, DataTableHead, DataTableRow, NoticeBox, SegmentedControl, Tag } from '@dhis2/ui';
import { Loading } from '@/lib/PageContainer';
import { VirtualTable } from '@/components/VirtualTable';
import { deploymentRunsOptions, registryOptions, runSummaryOptions, blueprintDiffOptions, blueprintVersionsOptions, packageFilesOptions } from '@/api/queries';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';
import { diffStateLabel, dispositionLabel, freshnessLabel } from '@/lib/labels';

type Mode = 'registry' | 'type' | 'file';

export function MetadataReview() {
  const { t } = useTranslation();
  const { projectId, workflowId } = useOutletContext<WorkspaceContext>();
  const [mode, setMode] = useState<Mode>('registry');
  const [searchParams, setSearchParams] = useSearchParams();
  const registry = useQuery(registryOptions(projectId));
  const runs = useQuery(deploymentRunsOptions(workflowId));
  const newest = (runs.data?.items ?? [])[0];
  const summary = useQuery(runSummaryOptions(newest?.id));
  const versions = useQuery(blueprintVersionsOptions(projectId));
  const latest = (versions.data?.items ?? []).slice(-1)[0];
  const diff = useQuery(
    blueprintDiffOptions(projectId, latest?.parentVersion ?? undefined, latest?.version ?? undefined),
  );
  // read only for the view that shows it: a package list is a build-record lookup, and the
  // registry and by-type views never need it
  const files = useQuery(packageFilesOptions(mode === 'file' ? projectId : undefined, latest?.version ?? undefined));

  const allRows = registry.data?.items ?? [];
  // UI-REV-002's "jump to the Registry entry" arrives as ?object=; the screen says what it is
  // showing and how to get back, rather than leaving a one-row table that looks like the whole model
  const focus = searchParams.get('object') ?? '';
  const rows = focus ? allRows.filter((row) => String(row.businessKey) === focus) : allRows;
  const byType = [...new Map(rows.map((row) => [row.dhis2Type ?? '—', (rows).filter((r) => (r.dhis2Type ?? '—') === (row.dhis2Type ?? '—'))])).entries()]
    .sort((a, b) => b[1].length - a[1].length);

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>{t('review.metadataTitle')}</h2>
          <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>{t('page.metadata')}</p>
        </div>
      </div>
      <SegmentedControl
        selected={mode}
        options={[
          { label: t('review.viewRegistry'), value: 'registry' },
          { label: t('review.viewByType'), value: 'type' },
          { label: t('review.viewByFile'), value: 'file' },
        ]}
        onChange={({ value }: { value: string }) => setMode(value as Mode)}
      />
      <p className="muted" style={{ fontSize: 12 }}>{t('review.fileSourceNote')}</p>

      {focus ? (
        <NoticeBox warning title={t('review.focusObject', { object: focus })}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <span>{rows.length ? t('review.focusBody') : t('review.focusMissing')}</span>
            <Button secondary onClick={() => setSearchParams({})}>
              {t('review.focusClear')}
            </Button>
          </div>
        </NoticeBox>
      ) : null}

      <Card>
        <div className="row" style={{ gap: 16, flexWrap: 'wrap' }}>
          <div>
            <strong>{t('review.statsTitle')}</strong>
            <div className="muted" style={{ fontSize: 12 }}>
              {t('review.statsFromRun')}: {newest?.id ?? '—'}
            </div>
          </div>
          {summary.data?.changeStats ? (
            <div className="row" style={{ gap: 12 }}>
              <Tag>{`+ ${summary.data.changeStats.create ?? 0}`}</Tag>
              <Tag>{`~ ${summary.data.changeStats.update ?? 0}`}</Tag>
              <Tag>{`- ${summary.data.changeStats.delete ?? 0}`}</Tag>
            </div>
          ) : (
            <span className="muted">{t('review.statsMissing')}</span>
          )}
          <Link to={`/projects/${projectId}/issues`}>{t('review.openIssues')}</Link>
        </div>
        {/* VALID is what the server calls a profile that still describes the instance it was read
            from; anything else means the answer may come from a different version than this run's. */}
        {summary.data?.capabilityFreshness?.state && summary.data.capabilityFreshness.state !== 'VALID' ? (
          <NoticeBox warning title={t('review.capabilityWarning')}>
            {t('review.capabilityFreshness', { value: freshnessLabel(summary.data.capabilityFreshness.state) })}
          </NoticeBox>
        ) : null}
      </Card>

      {registry.isLoading ? <Loading /> : null}
      {!registry.isLoading && !rows.length ? <p className="muted">{t('review.registryEmpty')}</p> : null}

      {/* UI-REV-006: the Registry is the 5,000-object list of this product (NFR-CAP-001), so it is
          windowed rather than rendered whole; the rows that exist are the rows in the DOM */}
      {mode === 'registry' && rows.length ? (
        <VirtualTable
          rows={rows}
          ariaLabel={t('review.registryList')}
          columns={[
            { key: 'businessKey', header: t('inspector.businessKey'), isRowHeader: true, cell: (row) => <span className="mono">{row.businessKey}</span> },
            { key: 'dhis2Type', header: t('common.type'), cell: (row) => row.dhis2Type ?? '—' },
            { key: 'disposition', header: t('review.disposition'), cell: (row) => <Tag>{dispositionLabel(row.disposition) || '—'}</Tag> },
            { key: 'uid', header: t('review.uid'), cell: (row) => <span className="mono">{row.uid ?? row.code ?? '—'}</span> },
            { key: 'managed', header: t('review.managed'), cell: (row) => (row.managed ? t('common.yes') : t('common.no')) },
            {
              key: 'decisionId',
              header: t('review.decision'),
              // The decision is the only place a design choice is recorded; the artifact itself
              // carries no decision id (UI-REV-001 points here for the same reason).
              cell: (row) =>
                row.decisionId ? (
                  <Link to={`/projects/${projectId}/decisions?ids=${encodeURIComponent(String(row.decisionId))}`} className="mono">
                    {row.decisionId}
                  </Link>
                ) : (
                  '—'
                ),
            },
          ]}
        />
      ) : null}

      {mode === 'type' && byType.length ? (
        <DataTable>
          <DataTableHead>
            <DataTableRow>
              <DataTableCell tag="th">{t('common.type')}</DataTableCell>
              <DataTableCell tag="th">{t('review.objectCount')}</DataTableCell>
              <DataTableCell tag="th">{t('review.disposition')}</DataTableCell>
            </DataTableRow>
          </DataTableHead>
          <DataTableBody>
            {byType.map(([type, items]) => (
              <DataTableRow key={type}>
                <DataTableCell>{type}</DataTableCell>
                <DataTableCell>{items.length}</DataTableCell>
                <DataTableCell>
                  {[...new Set(items.map((item) => dispositionLabel(item.disposition) || '—'))].join(', ')}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      ) : null}

      {mode === 'file' ? (
        files.isPending ? (
          <Loading />
        ) : !files.data?.files?.length ? (
          /* an unbuilt version answers with no files; showing an empty table would read as an
             empty package, which is a different claim (S5IF-BP17) */
          <NoticeBox warning title={t('review.noPackageTitle')}>
            {t('review.noPackageBody')}
          </NoticeBox>
        ) : (
          <div className="stack">
            <p className="muted mono" style={{ fontSize: 12 }}>
              {t('review.packageBuild')}: {files.data.buildId ?? '—'}
              {files.data.packageHash ? ` · ${files.data.packageHash}` : ''}
            </p>
            <VirtualTable
              rows={files.data.files ?? []}
              ariaLabel={t('review.viewByFile')}
              columns={[
                { key: 'path', header: t('review.filePath'), isRowHeader: true, cell: (file) => <span className="mono">{file.path}</span> },
                { key: 'role', header: t('review.fileRole'), cell: (file) => <Tag>{file.role ?? '—'}</Tag> },
                { key: 'dhis2Type', header: t('common.type'), cell: (file) => file.dhis2Type ?? '—' },
                {
                  key: 'objectCount',
                  header: t('review.objectCount'),
                  // null means the file holds no object at all — not zero objects
                  cell: (file) => (file.objectCount === null || file.objectCount === undefined ? '—' : file.objectCount),
                },
                { key: 'contentHash', header: t('review.fileHash'), cell: (file) => <span className="mono">{file.contentHash ?? '—'}</span> },
              ]}
            />
          </div>
        )
      ) : null}

      {diff.data ? (
        <div className="stack" style={{ gap: 4 }}>
          <strong>{t('review.diffTitle')}</strong>
          <div className="muted" style={{ fontSize: 12 }}>
            {diff.data.fromVersion ?? '—'} → {diff.data.toVersion ?? '—'}
          </div>
          {(diff.data.objects ?? []).slice(0, 25).map((object) => (
            <div key={`${object.businessKey}-${object.state}`} className="row" style={{ gap: 8 }}>
              <Tag>{diffStateLabel(object.state) || dispositionLabel(object.disposition) || '—'}</Tag>
              <span className="mono">{object.businessKey}</span>
              <span className="muted">{object.dhis2Type ?? ''}</span>
            </div>
          ))}
          {(diff.data.objects ?? []).length > 25 ? (
            <p className="muted" style={{ fontSize: 12 }}>{t('review.diffTruncated', { count: (diff.data.objects ?? []).length })}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
