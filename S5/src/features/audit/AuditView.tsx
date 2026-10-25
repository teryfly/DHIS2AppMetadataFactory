/*
 * UI-OPS-002: audit view — project and global audit, filtered by object / event type / operator /
 * time; full-chain integrity check with a break position; and a COMMIT traceability read.
 *
 * Every filter is sent to S1 rather than applied to the returned page. A page of the newest 50
 * records does not contain "what did this operator change last week", so filtering locally would
 * answer that question with an empty list and look like a correct answer.
 */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Button, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';
import { NoticeBar } from '@/components/NoticeBar';
import { TextInput } from '@/components/form';
import { auditOptions, commitTraceOptions, type AuditFilters } from '@/api/queries';
import { client, unwrap } from '@/api/client';
import { formatDate } from '@/lib/time';
import { capabilityLabel } from '@/lib/labels';
import { pushToast } from '@/components/toast';
import type { AuditRecordView, CommitTraceView } from '@/api/types';

export function AuditView() {
  const { t, i18n } = useTranslation();
  const { projectId } = useParams();
  const [filters, setFilters] = useState<AuditFilters>({});
  const [traceId, setTraceId] = useState('');
  // The trace is read on an explicit request: an execution id is not a search-as-you-type
  // subject, and a 404 per keystroke would bury the answer in error toasts.
  const [readTraceId, setReadTraceId] = useState('');
  const audit = useInfiniteQuery(auditOptions(projectId, filters));
  const trace = useQuery(commitTraceOptions(readTraceId || undefined));
  // `AuditVerifyView` answers `verified` / `brokenAt`; the field names this used to read
  // (`chainValid` / `breakSequence`) exist nowhere in the contract, so every verification came back
  // "broken at —" no matter what the chain looked like.
  const [verify, setVerify] = useState<{
    verified?: boolean;
    brokenAt?: number | null;
    firstSequence?: number | null;
    lastSequence?: number | null;
    recordCount?: number;
  } | null>(null);

  const rows = audit.data?.pages.flatMap((page) => page.items ?? []) ?? [];

  const verifyChain = async () => {
    try {
      // The endpoint has no project scope — it walks the chain by sequence range. Sending the
      // project id as a body used to look like a scoped check while answering a global one.
      const res = await unwrap<{
        verified?: boolean;
        brokenAt?: number | null;
        firstSequence?: number | null;
        lastSequence?: number | null;
        recordCount?: number;
      }>(() => client.POST('/api/v1/audit/verify', { body: {} as never }));
      setVerify(res);
    } catch {
      pushToast({ tone: 'error', title: t('error.title') });
    }
  };

  const setFilter = (key: keyof AuditFilters) => (value: string) =>
    setFilters((current) => ({ ...current, [key]: value || undefined }));

  return (
    <PageContainer title={t('audit.title')} description={t('page.audit')} helpKey="manage">
      <div className="stack" style={{ gap: 12, marginBottom: 16 }}>
        <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
          <TextInput label={t('audit.filterOperator')} value={filters.operatorName ?? ''} onChange={setFilter('operatorName')} />
          <TextInput label={t('audit.filterEvent')} value={filters.eventType ?? ''} onChange={setFilter('eventType')} />
          <TextInput label={t('audit.filterObject')} value={filters.objectId ?? ''} onChange={setFilter('objectId')} />
        </div>
        <div className="row" style={{ gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <TextInput label={t('audit.filterFrom')} type="datetime-local" value={filters.occurredAfter ?? ''} onChange={setFilter('occurredAfter')} />
          <TextInput label={t('audit.filterUntil')} type="datetime-local" value={filters.occurredBefore ?? ''} onChange={setFilter('occurredBefore')} />
          <Button
            onClick={() => {
              setFilters({});
            }}
          >
            {t('common.clearFilters')}
          </Button>
          <Button secondary onClick={verifyChain}>{t('audit.verifyChain')}</Button>
        </div>
      </div>

      {verify ? (
        <NoticeBar tone={verify.verified ? 'valid' : 'error'}>
          {verify.verified
            ? t('audit.chainOk', { from: verify.firstSequence ?? '—', to: verify.lastSequence ?? '—', count: verify.recordCount ?? 0 })
            : t('audit.chainBroken', { seq: verify.brokenAt ?? '—' })}
          <p className="muted" style={{ fontSize: 12 }}>{t('audit.chainScope')}</p>
        </NoticeBar>
      ) : null}

      {audit.isLoading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <EmptyState>{t('audit.empty')}</EmptyState>
      ) : (
        <>
          {/* The count is S1's, not the length of what happened to arrive: the filters are applied
              server-side and the cursor walks that filtered result, so "3 of 61" is a statement
              about the trail rather than about this page. */}
          <p className="muted" style={{ fontSize: 12 }}>
            {t('audit.shownOf', { shown: rows.length, total: audit.data?.pages[0]?.total ?? rows.length })}
          </p>
          <DataTable>
          <DataTableHead>
            <DataTableRow>
              <DataTableCell tag="th">{t('audit.title')}</DataTableCell>
              <DataTableCell tag="th">{t('operator.label')}</DataTableCell>
              <DataTableCell tag="th">{t('common.type')}</DataTableCell>
              <DataTableCell tag="th">{t('common.status')}</DataTableCell>
              <DataTableCell tag="th">{t('common.version')}</DataTableCell>
            </DataTableRow>
          </DataTableHead>
          <DataTableBody>
            {rows.map((r: AuditRecordView) => (
              <DataTableRow key={r.id}>
                <DataTableCell>{r.eventType}</DataTableCell>
                <DataTableCell>{r.operatorName ?? '—'}</DataTableCell>
                <DataTableCell className="mono">{r.objectId ?? '—'}</DataTableCell>
                <DataTableCell>{r.sourceSubsystem ? capabilityLabel(r.sourceSubsystem) : '—'}</DataTableCell>
                <DataTableCell className="mono">{formatDate(r.occurredAt, i18n.language)}</DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
          </DataTable>
          {audit.hasNextPage ? (
            <div className="row" style={{ gap: 8, marginTop: 8 }}>
              <Button secondary small loading={audit.isFetchingNextPage} onClick={() => void audit.fetchNextPage()}>
                {t('common.loadMore')}
              </Button>
            </div>
          ) : null}
        </>
      )}

      {/* UI-OPS-002: the COMMIT trace is read by execution id — the one identifier that ties the
          gate approval, the package, the import report and the audit records to one another. */}
      <div className="stack" style={{ gap: 8, marginTop: 24 }}>
        <h3 style={{ margin: 0 }}>{t('audit.commitTrace')}</h3>
        <div className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
          <TextInput label={t('audit.commitTraceExecution')} value={traceId} onChange={setTraceId} />
          <Button secondary disabled={!traceId} onClick={() => setReadTraceId(traceId)}>
            {t('audit.commitTraceRead')}
          </Button>
        </div>
        {readTraceId && trace.isFetching ? <Loading /> : null}
        {readTraceId && trace.isError ? <NoticeBar tone="error">{t('audit.commitTraceMissing')}</NoticeBar> : null}
        {trace.data ? (
          <CommitTrace view={trace.data} locale={i18n.language} />
        ) : null}
      </div>
    </PageContainer>
  );
}

function CommitTrace({ view, locale }: { view: CommitTraceView; locale: string }) {
  const { t } = useTranslation();
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 12, flexWrap: 'wrap' }}>
        <span>{t('audit.traceEnvironment')}: {view.environmentKey ?? '—'}</span>
        <span>{t('audit.traceBlueprint')}: {view.blueprintVersion ?? '—'}</span>
        <span>{t('audit.traceGate')}: {view.gateStatus ?? '—'}</span>
        <span>{t('audit.traceOperator')}: {view.commitOperator ?? '—'}</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {t('audit.tracePackage')}: <span className="mono">{view.packageArtifactId ?? '—'}</span>
        {' · '}
        {t('audit.traceImportReport')}: <span className="mono">{view.importReportArtifactId ?? '—'}</span>
      </div>
      {(view.auditTrail ?? []).length === 0 ? (
        <p className="muted">{t('audit.empty')}</p>
      ) : (
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {(view.auditTrail ?? []).map((record) => (
            <li key={record.id}>
              <span className="mono">{record.eventType}</span>
              {' — '}
              {record.operatorName ?? '—'}
              {' · '}
              {formatDate(record.occurredAt, locale)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
