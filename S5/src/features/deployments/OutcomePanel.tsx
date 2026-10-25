/*
 * UI-DEP-005 (总体 SRS 15.5, FR-EXE-011): after a COMMIT the operator has to see what actually
 * happened — the execution report, the DHIS2 ImportReport both normalised and raw, the
 * post-verification difference, and the snapshot this run committed against. All four are
 * artifacts; S1 answers their ids and this panel reads the bytes on demand, because a commit
 * summary should not stream every document to a browser that came to check one number.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { StatusBadge } from '@/components/StatusBadge';
import { JsonEditor } from '@/components/JsonEditor';
import { executionOptions, executionReportOptions, artifactContentOptions } from '@/api/queries';
import { formatDate } from '@/lib/time';
import type { RunSummaryView } from '@/api/types';
import { statusLabel } from '@/lib/labels';

export function OutcomePanel({ summary }: { summary: RunSummaryView }) {
  const { t, i18n } = useTranslation();
  const executionId = summary.execution?.executionId ?? undefined;
  // the reads switch themselves off without an execution, so a run that was never committed
  // asks for nothing -- and the hook order stays fixed for the moment it is committed
  const execution = useQuery(executionOptions(executionId));
  const report = useQuery(executionReportOptions(executionId));

  if (!executionId) return null;

  const doc = (label: string, artifactId: string | null | undefined) =>
    artifactId ? <ArtifactBlock key={label} label={label} artifactId={artifactId} /> : null;

  return (
    <Card>
      <section aria-label={t('deployments.outcome')} className="stack">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <Heading level={4} size="small">{t('deployments.outcome')}</Heading>
          <span className="row" style={{ gap: 8 }}>
            <span className="muted mono" style={{ fontSize: 12 }}>{executionId}</span>
            <StatusBadge status={execution.data?.status ?? summary.execution?.status ?? 'RUNNING'} />
          </span>
        </div>

        {execution.data?.issues?.length ? (
          <ul className="issue-list" aria-label={t('deployments.outcomeIssues')}>
            {execution.data.issues.map((issue, i) => (
              <li key={`${issue.objectKey ?? issue.businessKey ?? 'issue'}-${i}`}>
                <span className="mono">{issue.businessKey ?? issue.objectKey ?? issue.location ?? '—'}</span>
                {' — '}
                {issue.description ?? ''}
                {issue.suggestedAction ? <span className="muted"> · {issue.suggestedAction}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted" style={{ fontSize: 13 }}>{t('deployments.outcomeNoIssues')}</p>
        )}

        {execution.data?.steps?.length ? (
          <p className="muted mono" style={{ fontSize: 12 }}>
            {t('deployments.outcomeSteps')}: {execution.data.steps.length}
          </p>
        ) : null}

        {summary.snapshot?.artifactId ? (
          <p className="muted" style={{ fontSize: 13 }}>
            {t('deployments.snapshot')}: <span className="mono">{summary.snapshot.artifactId}</span>
            {summary.snapshot.status ? ` · ${statusLabel(summary.snapshot.status)}` : ''}
          </p>
        ) : null}

        {summary.verification ? (
          <p className="muted" style={{ fontSize: 13 }}>
            {t('deployments.postVerify')}:{' '}
            {summary.verification.consistent === null || summary.verification.consistent === undefined
              ? t('common.none')
              : summary.verification.consistent
                ? t('deployments.postVerifyConsistent')
                : t('deployments.postVerifyDiverged')}
            {summary.verification.lastVerifiedAt
              ? ` · ${formatDate(summary.verification.lastVerifiedAt, i18n.language)}`
              : ''}
          </p>
        ) : null}

        {/* the documents themselves, each read only when the operator opens it */}
        {doc(t('deployments.reportDocument'), report.data?.reportArtifactId)}
        {doc(t('deployments.importReportDocument'), report.data?.importReportArtifactId)}
        {doc(t('deployments.postVerifyDocument'), summary.verification?.postVerifyArtifactId)}
        {doc(t('deployments.snapshotDiffDocument'), summary.diffReportArtifactId)}
      </section>
    </Card>
  );
}

function ArtifactBlock({ label, artifactId }: { label: string; artifactId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const content = useQuery(artifactContentOptions(open ? artifactId : undefined));

  return (
    <div className="stack" style={{ gap: 4 }}>
      <button
        type="button"
        className="disclosure"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {label} <span className="mono muted">{artifactId}</span>
      </button>
      {open ? (
        content.isPending ? (
          <p className="muted" style={{ fontSize: 13 }}>{t('app.loading')}</p>
        ) : content.isError ? (
          <p className="text-critical" style={{ fontSize: 13 }}>{t('deployments.outcomeReadFailed')}</p>
        ) : (
          <JsonEditor value={String(content.data ?? '')} readOnly height="260px" />
        )
      ) : null}
    </div>
  );
}
