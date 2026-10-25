import { ref } from 'vue';
import { AppError } from '@/api/errors';

export type ToastKind = 'error' | 'warn' | 'success' | 'info';

export interface ToastItem {
  id: number;
  kind: ToastKind;
  /** i18n key when set (resolved by the host); raw text otherwise. */
  titleKey?: string;
  text?: string;
  errorCode?: string;
  details?: Record<string, unknown>;
  correlationId?: string;
}

const items = ref<ToastItem[]>([]);
let seq = 1;
const listeners = new Set<(t: ToastItem) => void>();

export const toasts = items;

function add(t: Omit<ToastItem, 'id'>): number {
  const item: ToastItem = { id: seq++, ...t };
  items.value = [item, ...items.value].slice(0, 4);
  listeners.forEach((l) => l(item));
  const ttl = item.kind === 'success' || item.kind === 'info' ? 2600 : 6000;
  setTimeout(() => dismiss(item.id), ttl);
  return item.id;
}

export function dismiss(id: number): void {
  items.value = items.value.filter((t) => t.id !== id);
}

/**
 * Drop the queued error toasts. A sign-in is what resolves the condition they report — the reads
 * that fired them were sent before any session existed — so leaving them up would show a wall of
 * "not authenticated" over a console that is now working.
 */
export function dismissErrors(): void {
  items.value = items.value.filter((t) => t.kind !== 'error');
}

export function pushError(err: unknown): number {
  if (err instanceof AppError) {
    return add({
      kind: 'error',
      titleKey: `errors.${err.errorCode}`,
      text: err.message,
      errorCode: err.errorCode,
      details: err.details,
      correlationId: err.correlationId,
    });
  }
  return add({ kind: 'error', text: (err as Error)?.message ?? String(err) });
}

export function pushSuccess(text: string, titleKey?: string): number {
  return add({ kind: 'success', text, titleKey });
}

export function pushWarn(text: string, titleKey?: string): number {
  return add({ kind: 'warn', text, titleKey });
}

export function pushInfo(text: string, titleKey?: string): number {
  return add({ kind: 'info', text, titleKey });
}

export function onToast(cb: (t: ToastItem) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
