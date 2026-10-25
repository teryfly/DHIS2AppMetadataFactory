/*
 * UI-RT: real-time refresh via SSE, with the discipline that events NEVER carry state —
 * they only make queries stale so TanStack re-reads S1 (UI-RT-002, UI-BR-02).
 *   UI-RT-001  reconnect carries the last received sequence; S1 replays what was missed.
 *   UI-RT-003  `stream.reset` → full refetch of the project.
 *   UI-RT-004  agent/execution progress is surfaced in the ephemeral progress store.
 *   UI-RT-005  offline banner after >10s disconnected; auto-catch-up on reconnect.
 */
import { useEffect, useRef, useState } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { eventsUrl } from '@/config/runtime';
import { qk, projectRootKeys } from '@/api/queryKeys';
import { useProgressStore } from './progress';
import { useConnectionStore } from '@/stores/connection';

export type SseStatus = 'connecting' | 'open' | 'offline';

// Full S1 event catalogue (S1 events/event_log.py). Unknown future types fall through to a
// safe "refresh everything workflow-scoped" behaviour.
const KNOWN_EVENT_TYPES = [
  'workflow.started', 'workflow.paused', 'workflow.resumed', 'workflow.completed', 'workflow.failed', 'workflow.updated',
  'stage.started', 'stage.waiting_input', 'stage.waiting_approval', 'stage.passed', 'stage.failed', 'stage.blocked',
  'stage.invalidated', 'stage.rerun', 'stage.manual_check',
  'agent.started', 'agent.completed', 'agent.progress',
  'question.created', 'question.answered',
  'gate.created', 'gate.approved', 'gate.rejected', 'gate.invalidated',
  'artifact.created', 'artifact.superseded',
  'input.parse',
  'validation.completed',
  'execution.started', 'execution.progress', 'execution.completed', 'execution.failed',
  'deployment_run.created', 'deployment_run.completed', 'deployment_run.failed', 'deployment_run.cancelled', 'deployment_run.outdated',
  'change_request.updated',
  'notification.created',
  'stream.reset',
];

/** Compute the query keys a single event should invalidate (never a full refresh unless reset). */
function invalidationsFor(
  eventType: string,
  ctx: { workflowId: string; projectId: string; runId?: string; gateId?: string; stageId?: string },
): Array<readonly unknown[]> {
  const { workflowId, projectId, runId, gateId, stageId } = ctx;
  const p = (prefix: string): boolean => eventType.startsWith(prefix);

  if (p('stream.reset')) return projectWideKeys(ctx);
  if (p('workflow.')) return [qk.workflow(projectId), qk.allowedActions(workflowId), qk.stages(workflowId)];
  if (p('stage.'))
    return [
      qk.stages(workflowId),
      qk.workflow(projectId),
      qk.allowedActions(workflowId),
      qk.validationIssues(workflowId),
      // The stage workbench reads the detail endpoint (it carries stageRuns, which the list
      // does not); a stage transition must refresh it or a finished run leaves the log frozen.
      ...(stageId ? [qk.stage(stageId)] : []),
    ];
  if (p('agent.')) return [qk.stages(workflowId), qk.allowedActions(workflowId), ...(runId ? [qk.agentRun(workflowId, runId)] : [])];
  if (p('question.')) return [qk.questions(workflowId), qk.stages(workflowId), qk.allowedActions(workflowId)];
  // A gate event names its gate: the GatePage reads the detail key ['gates', id] (and the
  // explanation under it), which no workflow-scoped prefix reaches.
  if (p('gate.')) {
    return [
      qk.gates(workflowId), qk.allowedActions(workflowId), qk.stages(workflowId), qk.gatesPending,
      ...(gateId ? [qk.gate(gateId)] : []),
    ];
  }
  if (p('artifact.')) return [qk.workflowArtifacts(workflowId), qk.blueprintDraft(projectId), qk.stages(workflowId)];
  // The parse's start and its outcome: the inputs list carries the status, the log page its calls.
  if (p('input.')) {
    return [qk.inputs(projectId), qk.stages(workflowId), qk.questions(workflowId), qk.nrr(workflowId)];
  }
  if (p('validation.')) return [qk.validationIssues(workflowId)];
  if (p('execution.')) return [qk.deploymentRuns(workflowId), ...(runId ? [qk.run(runId), qk.runSummary(runId)] : [qk.runList()]), qk.allowedActions(workflowId)];
  if (p('deployment_run.')) return [qk.deploymentRuns(workflowId), ...(runId ? [qk.run(runId)] : []), qk.allowedActions(workflowId)];
  if (p('change_request.')) return [qk.changeRequests(projectId)];
  if (p('notification.')) return [qk.notifications()];
  // Unknown future event → conservative scoped refresh.
  return [qk.workflow(projectId), qk.stages(workflowId), qk.allowedActions(workflowId)];
}

/**
 * The whole project scope for `stream.reset` / reconnect catch-up (UI-RT-003).
 * projectRootKeys alone misses the detail keys the screens read — stage, gate (and its
 * explanation subtree), run, artifact, question, execution live under their own roots.
 */
function projectWideKeys(ctx: { workflowId: string; projectId: string }): Array<readonly unknown[]> {
  return [
    ...projectRootKeys(ctx.projectId, ctx.workflowId),
    ['gates'],
    ['stages'],
    ['deployment-runs'],
    ['artifacts'],
    ['questions'],
    ['executions'],
  ];
}

function applyInvalidations(qc: QueryClient, keys: Array<readonly unknown[]>): void {
  for (const key of keys) {
    // `exact: false` prefix-invalidation: qk.workflow → ['projects', id, 'workflow']; passing a
    // shorter prefix refreshes the whole subtree.
    void qc.invalidateQueries({ queryKey: key as readonly unknown[], exact: false });
  }
}

interface Options {
  workflowId: string | undefined;
  projectId: string | undefined;
  /** Enable the stream (false on screens that don't need realtime). */
  enabled?: boolean;
}

export function useWorkflowEvents({ workflowId, projectId, enabled = true }: Options): { status: SseStatus } {
  const [status, setStatus] = useState<SseStatus>('connecting');
  const setConnection = useConnectionStore((s) => s.setStatus);
  const qc = useQueryClient();
  const lastSeqRef = useRef<number | undefined>(undefined);
  const upsertProgress = useProgressStore((s) => s.upsert);

  useEffect(() => {
    if (!enabled || !workflowId || !projectId) {
      setStatus('connecting');
      setConnection('idle');
      return;
    }

    let source: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let offlineTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    let openedOnce = false;

    const markOfflineAfter10s = () => {
      if (offlineTimer) return;
      offlineTimer = setTimeout(() => {
        if (!disposed) setStatus('offline'); // UI-RT-005: >10s gap → offline prompt
      }, 10_000);
    };

    const clearOfflineTimer = () => {
      if (offlineTimer) {
        clearTimeout(offlineTimer);
        offlineTimer = null;
      }
    };

    const connect = () => {
      if (disposed) return;
      setStatus((prev) => (prev === 'open' ? prev : 'connecting'));
      // Reconnect replays from the last sequence we saw (UI-RT-001). Fresh mount sends nothing
      // extra; S1 streams live from its retention head.
      const url = eventsUrl(workflowId, lastSeqRef.current);
      source = new EventSource(url);

      source.onopen = () => {
        if (disposed) return;
        setStatus('open');
        clearOfflineTimer();
        // On a reconnect that replays missed events, ensure the UI catches up fully once (UI-AC-03).
        if (openedOnce) applyInvalidations(qc, projectWideKeys({ workflowId, projectId }));
        openedOnce = true;
      };

      source.onerror = () => {
        if (disposed) return;
        // Close and manage reconnection manually so the resume carries the updated sequence —
        // S1 resumes on `?after=`, not the browser's Last-Event-ID header.
        source?.close();
        source = null;
        setStatus('connecting');
        markOfflineAfter10s();
        reconnectTimer = setTimeout(connect, 2_000);
      };

      for (const type of KNOWN_EVENT_TYPES) {
        source.addEventListener(type, (event: MessageEvent<string>) => {
          if (disposed) return;
          let body: { sequence?: number; payload?: Record<string, unknown> } = {};
          try {
            body = JSON.parse(event.data) as typeof body;
          } catch {
            /* malformed frame — ignore, refresh conservatively */
          }
          const seq = typeof body.sequence === 'number' ? body.sequence : Number(event.lastEventId);
          if (Number.isFinite(seq)) lastSeqRef.current = seq;

          const runId = (body.payload?.runId as string | undefined) ?? (body.payload?.deploymentRunId as string | undefined);
          const gateId = body.payload?.gateId as string | undefined;
          const eventStageId = body.payload?.stageId as string | undefined;

          if (type === 'agent.progress' || type === 'execution.progress') {
            const kind = type === 'agent.progress' ? 'agent' : 'execution';
            const stageId = body.payload?.stageId as string | undefined;
            // S1's frame names its run `agentRunId` (agent) or `executionId` (execution) and its
            // progress `completedFraction`; the widget is per stage, so the key prefers the stage
            // and the value carries the run for the cancel button to address.
            const frameRunId = (body.payload?.agentRunId as string | undefined) ?? (body.payload?.executionId as string | undefined);
            const frameData = (body.payload?.data as Record<string, unknown> | undefined) ?? {};
            const pkey = `${workflowId}:${kind}:${stageId ?? frameRunId ?? 'x'}`;
            upsertProgress(pkey, {
              workflowId,
              stageId,
              runId: frameRunId,
              currentStep: body.payload?.step as string | undefined,
              fraction: body.payload?.completedFraction as number | undefined,
              patchCount: frameData.patchCount as number | undefined,
              questionCount: frameData.questionCount as number | undefined,
              message: body.payload?.message as string | undefined,
              lastSeq: Number.isFinite(seq) ? seq : 0,
            });
            return; // progress frames are display-only; no refetch (UI-BR-02)
          }

          if (type === 'stream.reset') {
            // Full refresh of the whole project subtree (UI-RT-003).
            applyInvalidations(qc, projectWideKeys({ workflowId, projectId }));
            return;
          }

          applyInvalidations(qc, invalidationsFor(type, { workflowId, projectId, runId, gateId, stageId: eventStageId }));
        });
      }
    };

    connect();

    return () => {
      disposed = true;
      clearOfflineTimer();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
      setConnection('idle');
    };
  }, [workflowId, projectId, enabled, qc, upsertProgress, setConnection]);

  // Mirror the local transport status into the global connection store for the offline banner.
  useEffect(() => {
    if (enabled && workflowId && projectId) setConnection(status);
  }, [status, enabled, workflowId, projectId, setConnection]);

  return { status };
}
