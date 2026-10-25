/*
 * Ephemeral agent/execution progress for the live progress widget (UI-RT-004). Progress frames
 * are the ONLY source of in-flight step info, so it is held in memory — but it is transient
 * display data, NOT business state (UI-BR-02): it is never persisted, and any completion/status
 * change still arrives through a refetch of S1, not from these numbers.
 */
import { create } from 'zustand';

export interface ProgressLogEntry {
  seq: number;
  at: string;
  step?: string;
  message?: string;
  fraction?: number;
  patchCount?: number;
  questionCount?: number;
  runId?: string;
}

export interface ProgressInfo {
  workflowId: string;
  stageId?: string;
  /**
   * The run this frame belongs to — S1's `agentRunId`, or its `executionId` for an execution.
   * Cancelling needs the id: the action is gated without a target, but the endpoint is addressed
   * by run, so a frame that never named one leaves the cancel button with nothing to point at.
   */
  runId?: string;
  currentStep?: string;
  /**
   * S1's reporter (CP-EVT-002) answers a fraction of the run, never a step count — reading
   * `completed`/`total` off the frame is how the bar looked fine against the mock and stayed
   * empty against the server.
   */
  fraction?: number;
  patchCount?: number;
  questionCount?: number;
  message?: string;
  lastSeq: number;
  updatedAt: string;
  /**
   * The frames this run has already reported, newest last. Kept in memory beside the latest
   * value so the run-log drawer can show a timeline; the panel is display-only, and the
   * authoritative result still comes from the stage/agent-run reads.
   */
  log?: ProgressLogEntry[];
}

interface ProgressState {
  byWorkflow: Record<string, ProgressInfo>;
  upsert: (key: string, info: Partial<ProgressInfo> & { workflowId: string; lastSeq: number }) => void;
  clear: (key: string) => void;
}

/** Key by `${workflowId}:${kind}:${id}` so concurrent stage/run progress bars coexist. */
export const useProgressStore = create<ProgressState>((set) => ({
  byWorkflow: {},
  upsert: (key, info) =>
    set((s) => {
      const existing = s.byWorkflow[key];
      // Ignore out-of-order frames (monotonic sequence, UI-RT-001).
      if (existing && info.lastSeq <= existing.lastSeq) return s;
      const entry: ProgressLogEntry = {
        seq: info.lastSeq,
        at: new Date().toISOString(),
        step: info.currentStep,
        message: info.message,
        fraction: info.fraction,
        patchCount: info.patchCount,
        questionCount: info.questionCount,
        runId: info.runId,
      };
      const log = [...(existing?.log ?? []), entry].slice(-200);
      return {
        byWorkflow: {
          ...s.byWorkflow,
          [key]: { ...existing, ...info, log, updatedAt: new Date().toISOString() } as ProgressInfo,
        },
      };
    }),
  clear: (key) =>
    set((s) => {
      const next = { ...s.byWorkflow };
      delete next[key];
      return { byWorkflow: next };
    }),
}));
