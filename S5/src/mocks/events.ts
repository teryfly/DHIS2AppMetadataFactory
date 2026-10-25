/*
 * Mock SSE (UI-NFR-05): a stand-in EventSource so realtime refresh, progress and the offline
 * banner can be exercised with no S1. Frames mimic S1's shape (`event: <type>`, `data:` body with
 * `sequence`). It emits a few scripted events after connect, and exposes a control handle so
 * tests can push an arbitrary event or force a disconnect.
 */
import { WORKFLOW_ID } from './db';

type Listener = (ev: MessageEvent<string>) => void;

let seq = 100;

class MockEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly url: string;
  readyState = MockEventSource.CONNECTING;
  withCredentials = false;
  lastEventId = '';
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: Listener | null = null;

  private listeners = new Map<string, Set<Listener>>();
  private timers: number[] = [];

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.add(this);
    // Simulate the async open, then script a few events for the active workflow.
    const t = window.setTimeout(() => {
      this.readyState = MockEventSource.OPEN;
      this.onopen?.call(this, new Event('open'));
      this.emit('stage.waiting_approval', { stageId: 'ST-req' });
      this.emit('gate.created', { gateId: 'GT-1' });
    }, 30);
    this.timers.push(t);
  }

  addEventListener(type: string, cb: Listener): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(cb);
  }

  removeEventListener(type: string, cb: Listener): void {
    this.listeners.get(type)?.delete(cb);
  }

  emit(eventType: string, payload: Record<string, unknown>): void {
    const body = JSON.stringify({ eventType, workflowId: WORKFLOW_ID, sequence: ++seq, occurredAt: new Date().toISOString(), payload });
    const ev = new MessageEvent<string>(eventType, { data: body, lastEventId: String(seq) });
    this.lastEventId = String(seq);
    this.listeners.get(eventType)?.forEach((cb) => cb(ev));
    this.listeners.get('message')?.forEach((cb) => cb(ev));
  }

  close(): void {
    this.readyState = MockEventSource.CLOSED;
    MockEventSource.instances.delete(this);
    this.timers.forEach((t) => clearTimeout(t));
    this.listeners.clear();
  }

  /** Force an error so the hook's reconnect/offline path runs (UI-RT-005, UI-AC-03). */
  simulateDrop(): void {
    this.readyState = MockEventSource.CONNECTING;
    this.onerror?.call(this, new Event('error'));
  }

  static instances = new Set<MockEventSource>();
}

export interface MockSseControl {
  /** Push a custom event to every live mock stream (for assertions). */
  push(eventType: string, payload?: Record<string, unknown>): void;
  /** Drop the current stream to exercise reconnect + offline. */
  disconnect(): void;
  /** Restore the browser's real EventSource (no-op if not installed). */
  restore(): void;
}

export function installMockEventSource(): MockSseControl {
  const real = (globalThis as { EventSource?: typeof EventSource }).EventSource;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).EventSource = MockEventSource;
  return {
    push(eventType, payload = {}) {
      MockEventSource.instances.forEach((src) => src.emit(eventType, payload));
    },
    disconnect() {
      MockEventSource.instances.forEach((src) => src.simulateDrop());
    },
    restore() {
      if (real) (globalThis as { EventSource?: unknown }).EventSource = real;
    },
  };
}
