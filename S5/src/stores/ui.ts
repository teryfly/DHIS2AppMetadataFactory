/*
 * UI-APP-006: Zustand holds ONLY interface state — selection, panel layout, view mode, filters.
 * It MUST NOT store or infer business state (stage status, allowed actions, gate readiness): those
 * come only from TanStack Query reads of S1.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type WorkspaceViewMode = 'table' | 'graph' | 'registry' | 'diff' | 'preview';

interface UiState {
  /** Inspector: currently selected object key within the active workspace view. */
  selectedObjectKey: string | null;
  selectObject: (key: string | null) => void;

  /** Inspector collapse state. */
  inspectorOpen: boolean;
  toggleInspector: () => void;

  /** Main workspace view mode per stage type (remembered across navigation). */
  viewModeByStage: Record<string, WorkspaceViewMode>;
  setViewMode: (stageType: string, mode: WorkspaceViewMode) => void;

  /** Free-text + filter prefs for the current table/list. Persisted lightly per project. */
  filters: Record<string, string>;
  setFilter: (key: string, value: string) => void;
  clearFilters: () => void;

  /** Whether the operator-name dialog is open (pure UI state). */
  operatorDialogOpen: boolean;
  openOperatorDialog: () => void;
  closeOperatorDialog: () => void;

  /** Whether the change-my-password dialog is open (UI-AUTH-001). Same kind of state: it decides
   *  what is on screen, never whether the account may do anything. */
  passwordDialogOpen: boolean;
  openPasswordDialog: () => void;
  closePasswordDialog: () => void;

  /** Global help drawer. `helpTopic` picks the page-specific paragraph when there is one. */
  helpOpen: boolean;
  helpTopic: string | null;
  openHelp: (topic?: string) => void;
  closeHelp: () => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      selectedObjectKey: null,
      selectObject: (key) => set({ selectedObjectKey: key }),

      inspectorOpen: true,
      toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),

      viewModeByStage: {},
      setViewMode: (stageType, mode) =>
        set((s) => ({ viewModeByStage: { ...s.viewModeByStage, [stageType]: mode } })),

      filters: {},
      setFilter: (key, value) =>
        set((s) => ({ filters: { ...s.filters, [key]: value } })),
      clearFilters: () => set({ filters: {} }),

      operatorDialogOpen: false,
      openOperatorDialog: () => set({ operatorDialogOpen: true }),
      closeOperatorDialog: () => set({ operatorDialogOpen: false }),

      passwordDialogOpen: false,
      openPasswordDialog: () => set({ passwordDialogOpen: true }),
      closePasswordDialog: () => set({ passwordDialogOpen: false }),

      helpOpen: false,
      helpTopic: null,
      openHelp: (topic) => set({ helpOpen: true, helpTopic: topic ?? null }),
      closeHelp: () => set({ helpOpen: false, helpTopic: null }),
    }),
    {
      name: 'damf:ui',
      // Never persist selection or filters — only layout preferences.
      partialize: (s) => ({ inspectorOpen: s.inspectorOpen, viewModeByStage: s.viewModeByStage }),
    },
  ),
);
