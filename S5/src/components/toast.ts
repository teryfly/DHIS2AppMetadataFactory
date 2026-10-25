/*
 * Minimal toast store for surfaced API errors + action confirmations. Errors come from the
 * TanStack cache error hooks (via onApiError); messages carry a retry affordance only when the
 * ApiError is retryable (UI-REQ-002). COMMIT network failures use a dedicated non-auto entry
 * (UI-REQ-004), not this generic path.
 */
import { create } from 'zustand';

export interface Toast {
  id: string;
  tone: 'info' | 'success' | 'warning' | 'error';
  title: string;
  body?: string;
  correlationId?: string;
  retryable?: boolean;
}

interface ToastState {
  toasts: Toast[];
  push: (t: Omit<Toast, 'id'>) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

let counter = 0;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (t) => {
    const id = `t${++counter}`;
    set((s) => ({ toasts: [...s.toasts, { ...t, id }].slice(-4) }));
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

export function pushToast(t: Omit<Toast, 'id'>): string {
  return useToastStore.getState().push(t);
}
