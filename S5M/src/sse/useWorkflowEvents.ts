import { computed, onScopeDispose, ref, watch, toValue, type MaybeRefOrGetter } from 'vue';
import { useQueryClient } from '@tanstack/vue-query';
import { useDocumentVisibility, useOnline } from '@vueuse/core';
import { openWorkflowStream, type StreamHandle, type WorkflowEvent } from './transport';
import { qk, projectRootKeys } from '@/api/keys';
import { useProgressStore } from '@/stores/progress';
import { useConnectionStore } from '@/stores/connection';

interface Options {
  workflowId: MaybeRefOrGetter<string>;
  projectId: MaybeRefOrGetter<string>;
  enabled?: MaybeRefOrGetter<boolean>;
}

/**
 * Subscribes a workflow's SSE stream, translating events into targeted query invalidations — never
 * into UI state (UM-NET-003、UM-BR-02). Falls back to polling when SSE is unavailable or flaky (UM-NET-004).
 */
export function useWorkflowEvents(opts: Options) {
  const qc = useQueryClient();
  const progress = useProgressStore();
  const conn = useConnectionStore();
  const online = useOnline();
  const visibility = useDocumentVisibility();

  const enabled = computed(() => (opts.enabled === undefined ? true : toValue(opts.enabled)) && !!toValue(opts.workflowId));

  let stream: StreamHandle | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let retries = 0;
  const mode = ref<'off' | 'sse' | 'polling'>('off');

  const inv = (arg: { queryKey: readonly unknown[] }): void => {
    qc.invalidateQueries({ queryKey: arg.queryKey, exact: false });
  };

  function invalidateFor(e: WorkflowEvent): void {
    const wf = toValue(opts.workflowId);
    const pid = toValue(opts.projectId);
    const type = e.eventType;

    if (type === 'stream.reset') {
      for (const root of projectRootKeys(pid, wf)) qc.invalidateQueries({ queryKey: root, exact: false });
      qc.invalidateQueries({ queryKey: qk.gatesPending, exact: false });
      qc.invalidateQueries({ queryKey: qk.recentRuns, exact: false });
      conn.markLoaded();
      return;
    }
    if (type === 'agent.progress' || type === 'execution.progress') {
      const p = e.payload as Record<string, unknown>;
      const kind = type === 'agent.progress' ? 'agent' : 'execution';
      const id = String(p.stageId ?? p.runId ?? p.executionId ?? 'current');
      const fraction = typeof p.completedFraction === 'number' ? p.completedFraction : undefined;
      // S1 names the run `agentRunId` (agent) or `executionId` (execution); the cancel action
      // addresses the run by that id, so the frame must carry it into the store.
      const frameRunId = (p.agentRunId as string | undefined) ?? (p.executionId as string | undefined);
      progress.update(wf, kind, id, e.sequence, {
        currentStep: (p.step as string) ?? (p.currentStep as string),
        message: p.message as string,
        completed: p.completed as number,
        total: p.total as number,
        fraction,
        // An earlier frame's run id must survive a later frame that omits it — the cancel
        // button's only address would otherwise vanish mid-run.
        ...(frameRunId ? { runId: frameRunId } : {}),
      });
      return;
    }

    if (type.startsWith('workflow.')) inv({ queryKey: qk.workflow(wf) });
    if (type.startsWith('stage.')) {
      inv({ queryKey: qk.stages(wf) });
      const stageId = (e.payload as { stageId?: string }).stageId;
      if (stageId) inv({ queryKey: qk.stage(stageId) });
    }
    if (type.startsWith('agent.')) {
      inv({ queryKey: qk.stages(wf) });
      const runId = (e.payload as { runId?: string }).runId;
      // The run-log sheet reads this detail; without it the agent's final status/summary waits
      // for the panel's own poll (or never arrives if the panel is already showing a failure).
      if (runId) inv({ queryKey: qk.agentRun(wf, runId) });
    }
    if (type.startsWith('question.')) inv({ queryKey: qk.questions(wf) });
    if (type.startsWith('gate.')) inv({ queryKey: qk.gatesPending });
    if (type.startsWith('artifact.')) inv({ queryKey: qk.workflowArtifacts(wf) });
    // The parse's start and outcome both carry the per-input status the list shows.
    if (type.startsWith('input.')) inv({ queryKey: qk.inputs(pid) });
    if (type.startsWith('validation.')) inv({ queryKey: qk.validationIssues(wf) });
    if (type.startsWith('execution.')) inv({ queryKey: qk.recentRuns });
    if (type.startsWith('deployment_run.')) {
      inv({ queryKey: qk.deploymentRuns(wf) });
      inv({ queryKey: qk.recentRuns });
    }
    // The run console reads `['deployment-runs', id]` and its children (summary, bindings). Nothing
    // above touches that key, so a run going COMMIT left the checklist frozen on a live stream.
    const eventRunId = (e.payload as { runId?: string; deploymentRunId?: string }).runId
      ?? (e.payload as { deploymentRunId?: string }).deploymentRunId;
    if (eventRunId && (type.startsWith('deployment_run.') || type.startsWith('execution.'))) {
      inv({ queryKey: qk.run(eventRunId) });
    }
    if (type.startsWith('change_request.')) inv({ queryKey: qk.changeRequests(pid) });
    if (type === 'notification.created') inv({ queryKey: qk.notificationsList });

    // Always keep the action gate fresh on any change.
    inv({ queryKey: qk.allowedActions(wf) });
    conn.markLoaded();
  }

  function stopPolling(): void {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling(): void {
    if (pollTimer || mode.value === 'polling') return;
    mode.value = 'polling';
    conn.set('polling');
    const tick = (): void => {
      if (visibility.value !== 'visible') return;
      const wf = toValue(opts.workflowId);
      qc.invalidateQueries({ queryKey: qk.workflow(wf), exact: false });
      qc.invalidateQueries({ queryKey: qk.stages(wf), exact: false });
      qc.invalidateQueries({ queryKey: qk.allowedActions(wf), exact: false });
      qc.invalidateQueries({ queryKey: qk.gatesPending, exact: false });
      conn.markLoaded();
    };
    tick();
    pollTimer = setInterval(tick, 5000);
  }

  function stopStream(): void {
    stream?.close();
    stream = null;
  }

  function startSse(): void {
    if (stream) return;
    mode.value = 'sse';
    stream = openWorkflowStream(toValue(opts.workflowId), {
      onEvent: (e) => invalidateFor(e),
      onStatus: (s) => {
        if (s === 'open') {
          retries = 0;
          conn.set('open');
          stopPolling();
        } else if (s === 'offline') {
          retries += 1;
          conn.set('offline');
          // After repeated failures without ever opening, this browser/network can't hold a long
          // connection — downgrade to polling (UM-NET-004, UM-AC-07).
          if (retries >= 3) {
            stopStream();
            startPolling();
          }
        } else if (s === 'connecting') {
          conn.set('connecting');
        }
      },
    });
  }

  function teardown(): void {
    stopStream();
    stopPolling();
    mode.value = 'off';
    conn.set('idle');
  }

  function orchestrate(): void {
    if (!enabled.value || !online.value) {
      teardown();
      return;
    }
    if (visibility.value !== 'visible') {
      // page in background: pause the poll, keep SSE (cheap) but stop refresh
      stopPolling();
      return;
    }
    // If a previous downgrade left us polling, try to return to SSE when we come back to front.
    if (mode.value === 'polling') {
      retries = 0;
      stopPolling();
    }
    startSse();
  }

  watch([enabled, online, visibility, () => toValue(opts.workflowId)], orchestrate, { immediate: true });

  onScopeDispose(teardown);

  return { mode };
}
