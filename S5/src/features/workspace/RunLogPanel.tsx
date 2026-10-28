/*
 * A drawer, not a second stage screen: the run log is evidence about the stage the operator is
 * already looking at. It reads the stage's own run rows and the agent run S1 assembled from S3's
 * result + the question store + the event stream, and keeps the ephemeral SSE timeline beside them.
 *
 * Every attempt is kept: the list defaults to the newest one, and choosing an older attempt shows
 * that attempt's own persisted progress, result and issues instead of replacing it with the latest.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, LinearLoader, Tag } from '@dhis2/ui';
import { agentRunOptions, stageDetailOptions } from '@/api/queries';
import { useProgressStore } from '@/sse/progress';
import { formatDate } from '@/lib/time';
import { runStatusLabel, severityLabel } from '@/lib/labels';

type StageRunResult = { status?: string; summary?: string | null } | null | undefined;

export function RunLogPanel({
  workflowId,
  stageId,
  open,
  requested,
  stageStatus,
  onClose,
}: {
  workflowId: string;
  stageId: string;
  open: boolean;
  requested: boolean;
  stageStatus: string;
  /** Kept in the prop shape for callers; the attempt list is now read from stageRuns itself. */
  baselineRunIds?: string[];
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const progress = useProgressStore((s) => s.byWorkflow[`${workflowId}:agent:${stageId}`]);
  const detail = useQuery({
    ...stageDetailOptions(stageId, false),
    enabled: open && !!stageId,
    // While the drawer is open the operator is watching this run; a two-second read is cheap and
    // catches the case where the stage was BLOCKED before a rerun and the first refetch raced the
    // claim.
    refetchInterval: open ? 2_000 : false,
  });
  const runs = useMemo(() => detail.data?.stageRuns ?? [], [detail.data?.stageRuns]);
  const latestRun = runs.length ? runs[runs.length - 1] : undefined;
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  useEffect(() => {
    if (!runs.length) {
      setSelectedRunId(null);
      return;
    }
    const newest = runs[runs.length - 1]?.id;
    if (!newest) {
      setSelectedRunId(null);
      return;
    }
    setSelectedRunId((previous) => (previous && runs.some((run) => run.id === previous) ? previous : newest));
  }, [runs]);

  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? latestRun;
  const isLatest = selectedRun?.id === latestRun?.id;
  const result = (selectedRun?.result ?? {}) as StageRunResult;
  // The in-memory run id only belongs to the newest attempt; an older row must use the id S1
  // recorded on it, or selecting attempt 3 would silently show attempt 8's agent result.
  const runId = (isLatest ? progress?.runId : undefined) ?? selectedRun?.agentRunIds?.[0];
  const agentRun = useQuery({ ...agentRunOptions(workflowId, runId, open && isLatest), enabled: open && !!runId });
  const status = String(agentRun.data?.status ?? result?.status ?? (isLatest ? stageStatus : '') ?? '');
  const statusLabel =
    requested && !runId && ['', 'READY', 'PENDING'].includes(status)
      ? t('runLog.requested')
      : runStatusLabel(status) || status || t('runLog.statusUnknown');
  const live = !!isLatest && (status === 'RUNNING' || status === 'WAITING_INPUT' || (requested && ['', 'READY', 'PENDING'].includes(status)));
  const serverLog = selectedRun?.log ?? [];
  const liveLog = isLatest && requested ? progress?.log ?? [] : [];
  const log = serverLog.length ? serverLog : liveLog;
  const lastFraction = [...serverLog].reverse().find((entry) => entry.fraction != null)?.fraction;
  const fraction = Number(
    lastFraction ?? agentRun.data?.progress?.completedFraction ?? (isLatest ? progress?.fraction : undefined) ?? 0,
  );
  const step =
    [...serverLog].reverse().find((entry) => entry.step)?.step ??
    agentRun.data?.progress?.step ??
    (isLatest ? progress?.currentStep : undefined);
  const summary = (agentRun.data?.summary ?? result?.summary ?? null) as string | null;
  const issues = agentRun.data?.issues ?? [];
  const denialReasons = agentRun.data?.denialReasons ?? [];
  const rejected = agentRun.data?.rejectedArtifacts ?? [];
  const questions = agentRun.data?.questions ?? [];

  if (!open) return null;

  return (
    <div
      className="runlog-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside className="runlog-drawer" role="dialog" aria-modal="true" aria-labelledby="runlog-title">
        <header className="runlog-drawer__header">
          <div className="stack" style={{ gap: 4 }}>
            <h2 id="runlog-title">{t('runLog.title')}</h2>
            <span className="muted mono">{stageId}</span>
          </div>
          <Button secondary small onClick={onClose}>{t('common.close')}</Button>
        </header>

        <div className="runlog-drawer__body">
          <section className="runlog-section">
            <h3>{t('runLog.runs')} · {runs.length}</h3>
            {runs.length ? (
              <ul className="runlog-runs">
                {[...runs].reverse().map((run) => {
                  const runStatus = String((run.result as StageRunResult)?.status ?? '');
                  const selected = run.id === selectedRun?.id;
                  return (
                    <li key={run.id}>
                      <button
                        type="button"
                        className={`runlog-run${selected ? ' runlog-run--active' : ''}`}
                        aria-pressed={selected}
                        onClick={() => setSelectedRunId(run.id)}
                      >
                        <span className="runlog-run__attempt">{t('runLog.attempt', { n: run.attempt })}</span>
                        <Tag negative={runStatus === 'FAILED'} neutral={runStatus !== 'FAILED'}>
                          {runStatusLabel(runStatus) || runStatus || t('runLog.statusUnknown')}
                        </Tag>
                        <span className="runlog-run__time">
                          {formatDate(run.completedAt ?? run.createdAt, i18n.language)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="muted">{t('runLog.empty')}</p>
            )}
          </section>

          <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <Tag>{statusLabel}</Tag>
            {runId ? <span className="muted mono">{t('runLog.runId')}: {runId}</span> : null}
            {requested && !runId ? <Tag neutral>{t('runLog.requested')}</Tag> : null}
          </div>

          <div className="runlog-progress">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{t('runLog.progress')}</strong>
              <span className="muted">{Math.round(fraction * 100)}%</span>
            </div>
            <LinearLoader amount={Math.round(fraction * 100)} />
            <div className="muted" style={{ fontSize: 12 }}>
              {step ? `${t('runLog.latestStep')}: ${step}` : t('runLog.starting')}
            </div>
            {live ? <div className="muted" style={{ fontSize: 12 }}>{t('runLog.liveHint')}</div> : null}
          </div>

          <section className="runlog-section">
            <h3>{t('runLog.progress')}</h3>
            {log.length ? (
              <ol className="runlog-entries">
                {log.map((entry) => (
                  <li key={`${entry.seq}-${entry.at}`}>
                    <span className="runlog-entries__time">{formatDate(entry.at, i18n.language)}</span>
                    <span className="runlog-entries__message">
                      {entry.message || entry.step || t('runLog.empty')}
                    </span>
                    {entry.fraction != null ? (
                      <span className="runlog-entries__pct">{Math.round(entry.fraction * 100)}%</span>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">{requested && live ? t('runLog.waiting') : t('runLog.empty')}</p>
            )}
          </section>

          {summary ? (
            <section className="runlog-section">
              <h3>{t('runLog.summary')}</h3>
              <p>{summary}</p>
            </section>
          ) : null}

          {issues.length ? (
            <section className="runlog-section">
              <h3>{t('runLog.issues')}</h3>
              <ul className="runlog-list">
                {issues.map((issue, index) => (
                  <li key={`${issue.level ?? 'issue'}-${index}`}>
                    <Tag negative={issue.level === 'BLOCKING' || issue.level === 'ERROR'}>{severityLabel(issue.level)}</Tag>
                    <span>{issue.description}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {denialReasons.length ? (
            <section className="runlog-section">
              <h3>{t('runLog.denied')}</h3>
              <ul className="runlog-list">
                {denialReasons.map((reason, index) => <li key={index}>{reason}</li>)}
              </ul>
            </section>
          ) : null}

          {rejected.length ? (
            <section className="runlog-section">
              <h3>{t('runLog.rejected')}</h3>
              <ul className="runlog-list">
                {rejected.map((item, index) => {
                  const ref = typeof item.ref === 'string' ? item.ref : `#${index + 1}`;
                  const reason = typeof item.reason === 'string' ? item.reason : '';
                  return <li key={ref}><span className="mono">{ref}</span>{reason ? ` — ${reason}` : ''}</li>;
                })}
              </ul>
            </section>
          ) : null}

          {questions.length ? (
            <section className="runlog-section">
              <h3>{t('runLog.questions')} · {questions.length}</h3>
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
