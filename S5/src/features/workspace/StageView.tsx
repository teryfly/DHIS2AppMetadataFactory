/*
 * The stage work area. The action bar stays at the top of the stage, the narrative follows, and the
 * fallback authoring path is disclosed only when needed. Availability for every button still comes
 * only from allowed-actions.
 */
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useOutletContext, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, LinearLoader, NoticeBox, Modal, ModalTitle, ModalContent, ModalActions, SegmentedControl, Tag } from '@dhis2/ui';
import { StatusBadge } from '@/components/StatusBadge';
import { NoticeBar } from '@/components/NoticeBar';
import { Loading } from '@/lib/PageContainer';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { SectionCard } from '@/components/SectionCard';
import { Disclosure } from '@/components/Disclosure';
import { EmptyState } from '@/components/EmptyState';
import { Icon } from '@/components/icons';
import { QuestionCard } from './questions/QuestionCard';
import { ManualAuthoring, STAGE_SECTIONS } from './ManualAuthoring';
import { NrrCard } from './NrrCard';
import { RunLogPanel } from './RunLogPanel';
const ObjectGraph = lazy(() => import('@/features/review/ObjectGraph').then((module) => ({ default: module.ObjectGraph })));
import type { WorkspaceContext } from './WorkspaceContext';
import { stagesOptions, stageDetailOptions, questionsOptions, blueprintEntriesOptions } from '@/api/queries';
import {
  useRunStage,
  useResolveManualCheck,
  useCancelAgentRun,
  useSubmitForApproval,
} from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { useUiStore, type WorkspaceViewMode } from '@/stores/ui';
import { useProgressStore } from '@/sse/progress';
import { runStatusLabel, severityLabel } from '@/lib/labels';
import type { StageView as StageViewType } from '@/api/types';

export function StageView() {
  const { stageId = '' } = useParams();
  const ctx = useOutletContext<WorkspaceContext>();
  const { projectId, workflowId } = ctx;
  const { t } = useTranslation();
  const stagesQuery = useQuery(stagesOptions(workflowId));
  const listStage = (stagesQuery.data?.items ?? []).find((s) => s.id === stageId) as StageViewType | undefined;
  const [runRequested, setRunRequested] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  // The run rows that existed before this click. The log panel uses the difference to find the
  // new attempt without mistaking the previous attempt's EXECUTION_REPORT for this one.
  const baselineRuns = useRef<string[]>([]);
  // The list read has no stageRuns; the detail read carries them, and polls while a run is being
  // accepted or is still RUNNING so the log and the status chip do not freeze on the pre-run state.
  const detailQuery = useQuery(stageDetailOptions(stageId, runRequested || listStage?.status === 'RUNNING'));
  const stage = detailQuery.data ?? listStage;
  const questionsQuery = useQuery(questionsOptions(workflowId));

  // The route component can survive a stage-to-stage navigation; a run state belongs to one stage.
  useEffect(() => {
    setRunRequested(false);
    setLogOpen(false);
    baselineRuns.current = [];
  }, [stageId]);

  const stageQuestions = useMemo(() => {
    const list = (questionsQuery.data?.items ?? []).filter(
      (question) => question.stageId === stageId && question.status === 'OPEN',
    );
    return [...list].sort((a, b) => {
      const high = (b.severity === 'HIGH' ? 1 : 0) - (a.severity === 'HIGH' ? 1 : 0);
      if (high !== 0) return high;
      const answered = (a.status === 'ANSWERED' || !!a.answer ? 1 : 0) - (b.status === 'ANSWERED' || !!b.answer ? 1 : 0);
      return answered;
    });
  }, [questionsQuery.data, stageId]);

  if (stagesQuery.isLoading) return <Loading />;
  if (!stage) return <NoticeBar tone="error">{t('errors.NOT_FOUND')}</NoticeBar>;

  const feedback = stage.feedback;
  const isManual = stage.status === 'MANUAL_CHECK';
  const isRunning = stage.status === 'RUNNING';
  const showRunPanel = isRunning || runRequested;
  const denialKind = feedback?.kind === 'BUDGET_EXCEEDED' ? 'budget' : feedback?.kind === 'POLICY_DENIED' ? 'policy' : null;
  const openHigh = stageQuestions.filter(
    (question) => question.severity === 'HIGH' && question.status !== 'ANSWERED' && !question.answer,
  ).length;

  // Which businessKeys the rejection says are wrong, so the entry table can mark exactly them.
  const conflicts = new Map<string, ConflictInfo>();
  for (const issue of feedback?.issues ?? []) {
    const key = issue.businessKey || issue.objectKey;
    if (key) conflicts.set(key, { ruleId: issue.ruleId || undefined, suggestedAction: issue.suggestedAction || undefined });
  }

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="stage-toolbar">
        <div className="stage-toolbar__copy">
          <h2 className="stage-toolbar__title">
            {t(`stageTypes.${stage.stageType}`, { defaultValue: stage.stageType })}
            <button
              type="button"
              className="status-button"
              onClick={() => setLogOpen(true)}
              aria-label={t('runLog.open')}
              title={t('runLog.open')}
            >
              <StatusBadge status={stage.status} />
            </button>
            <Button secondary small onClick={() => setLogOpen(true)}>
              {t('runLog.open')}
            </Button>
          </h2>
          <p className="stage-toolbar__hint">
            {isRunning
              ? t('workspace.stageRunning')
              : isManual
                ? t('workspace.manualCheckTitle')
                : openHigh > 0
                  ? t('workspace.highOpenCount', { count: openHigh })
                  : t('page.stage')}
          </p>
        </div>
        <div className="stage-toolbar__actions">
          {!isManual && workflowId ? (
            <RunActions
              workflowId={workflowId}
              stageId={stageId}
              onRequested={() => {
                baselineRuns.current = (detailQuery.data?.stageRuns ?? []).map((run) => run.id);
                setRunRequested(true);
                setLogOpen(true);
              }}
              onFailed={() => {
                setRunRequested(false);
                setLogOpen(false);
              }}
            />
          ) : null}
          {workflowId ? <SubmitApproval workflowId={workflowId} projectId={projectId} stageId={stageId} /> : null}
        </div>
      </div>

      {feedback?.reason && ['FAILED', 'BLOCKED', 'INVALIDATED'].includes(stage.status) ? (
        <NoticeBar tone="warning" title={t('workspace.rejectionFeedback')}>
          <div className="stack">
            <span>{feedback.reason}</span>
            {feedback.sourceStage ? (
              <span className="muted">
                {t('workspace.failureFromStage')}: {t(`stageTypes.${feedback.sourceStage}`, { defaultValue: feedback.sourceStage })}
              </span>
            ) : null}
            {feedback.issues?.length ? (
              <ul>
                {feedback.issues.map((issue, index) => (
                  <li key={issue.issueId || `feedback-issue-${index}`}>
                    <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Tag negative={issue.level === 'BLOCKING'} neutral={issue.level !== 'BLOCKING'}>
                        {severityLabel(issue.level)}
                      </Tag>
                      {issue.ruleId ? <span className="muted mono">{issue.ruleId}</span> : null}
                    </div>
                    <div>
                      <span className="mono">{issue.businessKey ?? issue.objectKey ?? ''}</span>
                      {issue.businessKey || issue.objectKey ? ' — ' : ''}
                      {issue.description}
                    </div>
                    {issue.suggestedAction ? (
                      <div className="muted">{t('workspace.suggestedAction')}: {issue.suggestedAction}</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </NoticeBar>
      ) : null}

      {denialKind ? (
        <NoticeBar tone="error" title={t('workspace.deniedTitle')}>
          <div className="stack">
            <span>{denialKind === 'budget' ? t('workspace.deniedBudget') : t('workspace.deniedPolicy')}</span>
            {feedback?.rejectedArtifacts?.length ? (
              <span className="muted mono">
                {feedback.rejectedArtifacts
                  .map((artifact) => {
                    const value = artifact as { artifactId?: unknown; id?: unknown; ref?: unknown };
                    return typeof value.artifactId === 'string'
                      ? value.artifactId
                      : typeof value.id === 'string'
                        ? value.id
                        : typeof value.ref === 'string'
                          ? value.ref
                          : JSON.stringify(artifact);
                  })
                  .join(', ')}
              </span>
            ) : null}
            <span className="muted">{t('workspace.deniedGuide')}</span>
          </div>
        </NoticeBar>
      ) : null}

      {showRunPanel && workflowId ? (
        <AgentProgressPanel
          workflowId={workflowId}
          stageId={stageId}
          stageStatus={stage.status}
          requested={runRequested}
          onOpenLog={() => setLogOpen(true)}
          onDismiss={() => setRunRequested(false)}
        />
      ) : null}
      {isManual && workflowId ? <ManualCheckPanel workflowId={workflowId} stageId={stageId} /> : null}

      {stageQuestions.length > 0 ? (
        <SectionCard
          title={`${t('questions.title')} · ${stageQuestions.length}`}
          description={t('questions.highBlockingBody')}
        >
          <QuestionCard questions={stageQuestions} workflowId={workflowId} />
        </SectionCard>
      ) : null}

      {stage.stageType === 'REQUIREMENTS' && workflowId ? (
        <NrrCard workflowId={workflowId} projectId={projectId} />
      ) : null}

      <EntriesCard projectId={projectId} stageType={stage.stageType} conflicts={conflicts} />

      <Disclosure
        label={t('manual.title')}
        description={t('manual.jsonHint')}
      >
        <ManualAuthoring
          stageId={stageId}
          workflowId={workflowId ?? ''}
          projectId={projectId}
          stageType={stage.stageType}
        />
      </Disclosure>

      {workflowId ? (
        <RunLogPanel
          workflowId={workflowId}
          stageId={stageId}
          open={logOpen}
          requested={runRequested}
          stageStatus={stage.status}
          baselineRunIds={baselineRuns.current}
          onClose={() => setLogOpen(false)}
        />
      ) : null}
    </div>
  );
}

function SubmitApproval({ workflowId, projectId, stageId }: { workflowId: string; projectId: string; stageId: string }) {
  const { t } = useTranslation();
  const approval = useSubmitForApproval(workflowId, projectId);
  return (
    <AllowedActionButton
      workflowId={workflowId}
      action={ACTION.stageSubmitForApproval}
      targetId={stageId}
      tone="secondary"
      loading={approval.isPending}
      onExecute={() => approval.mutateAsync({ stageId })}
    >
      {t('workspace.submitApproval')}
    </AllowedActionButton>
  );
}

function RunActions({
  workflowId,
  stageId,
  onRequested,
  onFailed,
}: {
  workflowId: string;
  stageId: string;
  onRequested: () => void;
  onFailed: () => void;
}) {
  const run = useRunStage(workflowId);
  const clearProgress = useProgressStore((s) => s.clear);
  const { t } = useTranslation();
  const start = (rerun?: boolean) => {
    // A parse also writes progress frames under this stage key; clearing them keeps the new run's
    // log from opening on yesterday's parse steps.
    clearProgress(`${workflowId}:agent:${stageId}`);
    onRequested();
    return run.mutateAsync({ stageId, rerun }).catch((error) => {
      onFailed();
      throw error;
    });
  };
  return (
    <>
      <AllowedActionButton
        workflowId={workflowId}
        action={ACTION.stageRun}
        targetId={stageId}
        loading={run.isPending}
        onExecute={() => start(false)}
      >
        <Icon name="play" size={15} />
        {t('workspace.runAgent')}
      </AllowedActionButton>
      <AllowedActionButton
        workflowId={workflowId}
        action={ACTION.stageRerun}
        targetId={stageId}
        tone="secondary"
        onExecute={() => start(true)}
      >
        {t('workspace.rerunAgent')}
      </AllowedActionButton>
    </>
  );
}

function AgentProgressPanel({
  workflowId,
  stageId,
  stageStatus,
  requested,
  onOpenLog,
  onDismiss,
}: {
  workflowId: string;
  stageId: string;
  stageStatus: string;
  requested: boolean;
  onOpenLog: () => void;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const progress = useProgressStore((s) => s.byWorkflow[`${workflowId}:agent:${stageId}`]);
  const cancel = useCancelAgentRun(workflowId);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const ratio = progress?.fraction ?? 0;
  const runId = progress?.runId;
  const live = stageStatus === 'RUNNING' || (requested && ['READY', 'PENDING', ''].includes(stageStatus));

  return (
    <NoticeBox title={live ? t('workspace.agentRunning') : t('runLog.resultTitle')}>
      <div className="stack">
        <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tag>{live && stageStatus !== 'RUNNING' ? t('runLog.requested') : runStatusLabel(stageStatus) || stageStatus}</Tag>
          {!live && requested ? <span className="muted" style={{ fontSize: 12 }}>{t('runLog.requested')}</span> : null}
        </div>
        {live ? <span className="muted">{progress?.currentStep ? `${t('workspace.agentProgress')}: ${progress.currentStep}` : t('runLog.starting')}</span> : null}
        <LinearLoader amount={Math.round(ratio * 100)} />
        {progress?.patchCount != null || progress?.questionCount != null ? (
          <span className="muted" style={{ fontSize: 12 }}>
            {t('workspace.artifacts', { count: progress?.patchCount ?? 0 })} · {t('questions.title')}: {progress?.questionCount ?? 0}
          </span>
        ) : null}
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <Button secondary small onClick={onOpenLog}>{t('runLog.open')}</Button>
          {live && runId ? (
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.agentCancel}
              tone="destructive"
              loading={cancel.isPending}
              onExecute={() => setConfirmOpen(true)}
            >
              {t('workspace.agentCancel')}
            </AllowedActionButton>
          ) : null}
          {!live ? <Button secondary small onClick={onDismiss}>{t('runLog.dismiss')}</Button> : null}
        </div>
      </div>

      {confirmOpen ? (
        <Modal position="middle" hide={false} onClose={() => setConfirmOpen(false)}>
          <ModalTitle>{`${t('workspace.agentCancel')} · ${t('common.confirm')}`}</ModalTitle>
          <ModalContent>
            <div className="stack">
              <span className="mono">{t('workspace.agentCancelRun')}: {runId}</span>
              <span className="mono">{t('workspace.contextStage')}: {stageId}</span>
              <span className="muted">{t('workspace.agentCancelBody')}</span>
            </div>
          </ModalContent>
          <ModalActions>
            <Button secondary onClick={() => setConfirmOpen(false)}>{t('common.cancel')}</Button>
            <Button
              destructive
              loading={cancel.isPending}
              onClick={() => cancel.mutate({ runId: runId ?? '' }, { onSuccess: () => setConfirmOpen(false) })}
            >
              {t('workspace.agentCancel')}
            </Button>
          </ModalActions>
        </Modal>
      ) : null}
    </NoticeBox>
  );
}

function ManualCheckPanel({ workflowId, stageId }: { workflowId: string; stageId: string }) {
  const { t } = useTranslation();
  const [note, setNote] = useState('');
  const resolve = useResolveManualCheck(workflowId);
  const act = (outcome: 'PASSED' | 'FAILED') => resolve.mutateAsync({ stageId, note, outcome });
  return (
    <SectionCard title={t('workspace.manualCheckTitle')} tone="warning">
      <div className="stack">
        <div>
          <label className="muted" style={{ fontSize: 12 }} htmlFor="mc-note">{t('workspace.manualCheckNote')}</label>
          <textarea id="mc-note" className="field__input field__textarea" value={note} onChange={(event) => setNote(event.target.value)} rows={3} />
        </div>
        <div className="row">
          <AllowedActionButton workflowId={workflowId} action={ACTION.stageResolveManualCheck} targetId={stageId} loading={resolve.isPending} onExecute={() => act('PASSED')}>
            {t('workspace.manualCheckPass')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action={ACTION.stageResolveManualCheck} targetId={stageId} tone="destructive" onExecute={() => act('FAILED')}>
            {t('workspace.manualCheckFail')}
          </AllowedActionButton>
        </div>
      </div>
    </SectionCard>
  );
}

//: Entries are read from the *draft*, not from the transition view (`draft.document` is
//: deliberately absent; the whole document is S5IF-UP03). STAGE_SECTIONS lives with the manual
//: editor, which seeds from the same read.

type DraftEntry = Record<string, unknown> & { _section?: string; businessKey?: string };

/** What one freeze/Gate issue says about a businessKey, for the row highlight. */
export type ConflictInfo = { ruleId?: string; suggestedAction?: string };

function localized(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && typeof (value as { default?: unknown }).default === 'string') {
    return String((value as { default: string }).default);
  }
  return '';
}

function entrySummary(entry: DraftEntry): string {
  if (entry.cardinality || entry.from || entry.to) {
    return [entry.from, entry.to].filter(Boolean).join(' → ');
  }
  const prose =
    localized(entry.displayName) ||
    localized(entry.name) ||
    (typeof entry.statement === 'string' ? entry.statement : '') ||
    (typeof entry.text === 'string' ? entry.text : '') ||
    (typeof entry.purpose === 'string' ? entry.purpose : '') ||
    (typeof entry.description === 'string' ? entry.description : '');
  const priority = typeof entry.priority === 'string' ? entry.priority : '';
  return [prose, priority].filter(Boolean).join(' · ') || String(entry.businessKey ?? '—');
}

function EntriesCard({ projectId, stageType, conflicts }: { projectId: string; stageType: string; conflicts: Map<string, ConflictInfo> }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const sections = STAGE_SECTIONS[stageType] ?? [];
  const draftQuery = useQuery({
    ...blueprintEntriesOptions(projectId, sections, 'DRAFT'),
    enabled: !!projectId && sections.length > 0,
  });
  const view = useUiStore((s) => s.viewModeByStage[stageType]);
  const setView = useUiStore((s) => s.setViewMode);
  const current: WorkspaceViewMode = view ?? 'table';
  const allEntries = (draftQuery.data?.entries ?? []) as DraftEntry[];
  const [onlyConflicts, setOnlyConflicts] = useState(false);
  const entries = onlyConflicts ? allEntries.filter((e) => e.businessKey && conflicts.has(e.businessKey)) : allEntries;
  const hasConflicts = conflicts.size > 0;

  return (
    <SectionCard
      title={t('workspace.stageEntries')}
      description={hasConflicts ? t('workspace.stageEntriesConflictHint') : t('workspace.stageEntriesHint')}
      actions={
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          {hasConflicts ? (
            <label className="row" style={{ gap: 4, fontSize: 12, cursor: 'pointer' }}>
              <input type="checkbox" checked={onlyConflicts} onChange={(event) => setOnlyConflicts(event.target.checked)} />
              {t('workspace.conflictOnly')}
            </label>
          ) : null}
          <SegmentedControl
            selected={current}
            options={[
              { label: t('workspace.viewTable'), value: 'table' },
              { label: t('workspace.viewGraph'), value: 'graph' },
              { label: t('workspace.viewRegistry'), value: 'registry' },
              { label: t('workspace.viewDiff'), value: 'diff' },
            ]}
            onChange={({ value }: { value: string }) => {
              if (value === 'registry' || value === 'diff') {
                navigate(`/projects/${encodeURIComponent(projectId)}/metadata`);
                return;
              }
              setView(stageType, value as WorkspaceViewMode);
            }}
          />
        </div>
      }
    >
      {current === 'graph' ? (
        <Suspense fallback={<Loading />}>
          <ObjectGraph />
        </Suspense>
      ) : draftQuery.isLoading ? (
        <Loading />
      ) : entries.length === 0 ? (
        <EmptyState icon="file" title={t('review.noArtifacts')}>
          {t('workspace.currentStageHint')}
        </EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="record-table">
            <thead>
              <tr>
                <th>{t('inspector.objectType')}</th>
                <th>{t('inspector.businessKey')}</th>
                <th>{t('common.description')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, index) => {
                const conflict = entry.businessKey ? conflicts.get(entry.businessKey) : undefined;
                return (
                  <tr
                    key={`${entry._section ?? ''}:${entry.businessKey ?? index}`}
                    className={conflict ? 'record-table__row record-table__row--conflict' : 'record-table__row'}
                  >
                    <td className="mono">{String(entry._section ?? '—')}</td>
                    <td className="mono">
                      {String(entry.businessKey ?? '—')}
                      {conflict ? (
                        <div className="row" style={{ gap: 6, marginTop: 4, alignItems: 'center' }}>
                          <Tag negative>{t('workspace.conflictTag')}</Tag>
                          {conflict.ruleId ? <span className="muted mono" style={{ fontSize: 11 }}>{conflict.ruleId}</span> : null}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      {entrySummary(entry)}
                      {conflict?.suggestedAction ? (
                        <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                          {t('workspace.suggestedAction')}: {conflict.suggestedAction}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </SectionCard>
  );
}
