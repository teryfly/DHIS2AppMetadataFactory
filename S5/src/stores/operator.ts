/*
 * UI-OP-001: the operator name is entered by the user (MVP replaces login, D-16), stored in the
 * browser, shown in the top bar and editable at any time. UI-OP-003: every state-changing request
 * auto-attaches `X-Operator`. UI-OP-002: with no name set, all mutating buttons are disabled.
 *
 * This store holds ONLY the operator name — a UI/browser fact, not business state (UI-APP-006).
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

const STORAGE_KEY = 'damf:operator';

interface OperatorState {
  name: string | null;
  setName: (name: string) => void;
  clear: () => void;
}

export const useOperatorStore = create<OperatorState>()(
  persist(
    (set) => ({
      name: null,
      setName: (name) => set({ name: name.trim() || null }),
      clear: () => set({ name: null }),
    }),
    { name: STORAGE_KEY },
  ),
);

/** Synchronous read for the API client middleware (outside React render). */
export function getOperatorName(): string | null {
  return useOperatorStore.getState().name;
}

export function hasOperator(): boolean {
  const n = getOperatorName();
  return !!n && n.length > 0;
}
