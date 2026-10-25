import { defineStore } from 'pinia';
import { reactive } from 'vue';

// In-memory only, never persisted (UM-BR-02). Progress events drive the running-stage bar; business
// status still comes from REST re-reads, this only shows step-level progress (UM-STG-004).
export interface ProgressLogEntry {
  seq: number;
  at: string;
  step?: string;
  message?: string;
  fraction?: number;
  runId?: string;
}

export interface ProgressEntry {
  currentStep?: string;
  message?: string;
  completed?: number;
  total?: number;
  fraction?: number;
  /** The S1 run this progress belongs to (agentRunId / executionId); the cancel action needs it to address the run. */
  runId?: string;
  lastSeq: number;
  /** The frames this run has already reported, newest last; the run-log sheet renders this timeline. */
  log?: ProgressLogEntry[];
}

export const useProgressStore = defineStore('progress', () => {
  const entries = reactive<Record<string, ProgressEntry>>({});

  function update(workflowId: string, kind: 'agent' | 'execution', id: string, seq: number, patch: Omit<ProgressEntry, 'lastSeq' | 'log'>): void {
    const key = `${workflowId}:${kind}:${id}`;
    const existing = entries[key];
    if (existing && existing.lastSeq >= seq) return; // drop out-of-order frames
    const entry: ProgressLogEntry = {
      seq,
      at: new Date().toISOString(),
      step: patch.currentStep,
      message: patch.message,
      fraction: patch.fraction,
      runId: patch.runId,
    };
    entries[key] = {
      ...(existing ?? { lastSeq: 0 }),
      ...patch,
      lastSeq: seq,
      log: [...(existing?.log ?? []), entry].slice(-200),
    };
  }

  function get(workflowId: string, kind: 'agent' | 'execution', id: string): ProgressEntry | undefined {
    return entries[`${workflowId}:${kind}:${id}`];
  }

  function clear(key: string): void {
    delete entries[key];
  }

  function clearWorkflow(workflowId: string): void {
    for (const k of Object.keys(entries)) if (k.startsWith(`${workflowId}:`)) delete entries[k];
  }

  return { entries, update, get, clear, clearWorkflow };
});
