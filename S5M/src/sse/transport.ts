import { eventsUrl } from '@/config/runtime';

export type SseStatus = 'connecting' | 'open' | 'offline';

export interface WorkflowEvent {
  eventType: string;
  workflowId: string;
  sequence: number;
  occurredAt: string;
  payload: Record<string, unknown>;
}

// Full catalogue mirrored from S1 (see S5 SSE map). Unknown types fall through to a conservative
// workflow refresh in the consumer.
export const KNOWN_EVENT_TYPES = [
  'workflow.started', 'workflow.paused', 'workflow.resumed', 'workflow.completed', 'workflow.failed', 'workflow.updated',
  'stage.started', 'stage.waiting_input', 'stage.waiting_approval', 'stage.passed', 'stage.failed', 'stage.blocked', 'stage.invalidated', 'stage.rerun', 'stage.manual_check',
  'agent.started', 'agent.completed', 'agent.progress',
  'question.created', 'question.answered',
  'gate.created', 'gate.approved', 'gate.rejected', 'gate.invalidated',
  'artifact.created', 'artifact.superseded',
  'input.parse',
  'validation.completed',
  'execution.started', 'execution.progress', 'execution.completed', 'execution.failed',
  'deployment_run.created', 'deployment_run.completed', 'deployment_run.failed', 'deployment_run.cancelled', 'deployment_run.outdated',
  'change_request.updated', 'notification.created', 'stream.reset',
];

export interface StreamHandlers {
  onEvent: (e: WorkflowEvent) => void;
  onStatus: (s: SseStatus) => void;
}

export interface StreamHandle {
  close: () => void;
  lastSeq: () => number;
}

/**
 * One EventSource per workflow. Reconnects from the last sequence (UM-NET-003); reports status so
 * the caller can fall back to polling (UM-NET-004).
 */
export function openWorkflowStream(workflowId: string, handlers: StreamHandlers): StreamHandle {
  let lastSeq = 0;
  let es: EventSource | null = null;
  let closed = false;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  // A fixed 2 s retry turned a server that closes the stream (S1 restart, proxy idle timeout) into
  // a request every 2 s per open view. Exponential with jitter, capped, reset on a successful open.
  function nextDelay(): number {
    const base = Math.min(30000, 1000 * 2 ** attempt);
    attempt += 1;
    return base + Math.random() * Math.min(1000, base / 2);
  }

  function connect(): void {
    if (closed) return;
    handlers.onStatus('connecting');
    es = new EventSource(eventsUrl(workflowId, lastSeq), { withCredentials: false });

    es.onopen = () => {
      attempt = 0;
      handlers.onStatus('open');
    };

    const handle = (raw: MessageEvent): void => {
      try {
        const data = JSON.parse(raw.data) as WorkflowEvent;
        if (typeof data.sequence === 'number' && data.sequence > lastSeq) lastSeq = data.sequence;
        handlers.onEvent(data);
      } catch {
        /* ignore malformed frame */
      }
    };

    for (const type of KNOWN_EVENT_TYPES) es.addEventListener(type, handle as EventListener);
    es.addEventListener('message', handle as EventListener);

    es.onerror = () => {
      if (closed) return;
      handlers.onStatus('offline');
      es?.close();
      es = null;
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, nextDelay());
    };
  }

  connect();

  return {
    lastSeq: () => lastSeq,
    close: () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
      es = null;
    },
  };
}
