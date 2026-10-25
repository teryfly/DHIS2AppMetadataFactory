import { defineStore } from 'pinia';
import { ref } from 'vue';
import { LS, readJSON, writeJSON } from '@/lib/storage';

export interface RecentObject {
  kind: 'project' | 'stage' | 'gate' | 'question' | 'run' | 'artifact' | 'environment';
  id: string;
  label: string;
  at: number;
}

/**
 * UM-NAV-004's other half: a remembered object has to be tappable, so the kind it was stored under
 * decides where it goes. `router.afterEach` writes exactly these kinds — a new one there needs an
 * entry here, or the panel lists a row that goes nowhere.
 */
export const RECENT_TARGETS: Record<RecentObject['kind'], (id: string) => string> = {
  project: (id) => `/projects/${encodeURIComponent(id)}`,
  stage: (id) => `/stages/${encodeURIComponent(id)}`,
  gate: (id) => `/gates/${encodeURIComponent(id)}`,
  question: (id) => `/questions/${encodeURIComponent(id)}`,
  run: (id) => `/runs/${encodeURIComponent(id)}`,
  artifact: (id) => `/artifacts/${encodeURIComponent(id)}`,
  environment: (id) => `/environments/${encodeURIComponent(id)}`,
};

interface UiState {
  recent: RecentObject[];
  filters: Record<string, unknown>;
  snoozed: Record<string, number>;
  dataClassByProject: Record<string, string>;
  lastProjectId: string | null;
}

const defaults: UiState = { recent: [], filters: {}, snoozed: {}, dataClassByProject: {}, lastProjectId: null };

// UI-only facts, never business state (UM-P-05, §13). "稍后处理" hides a todo card locally for 24h.
export const useUiStore = defineStore('ui', () => {
  const saved = readJSON<Partial<UiState>>(LS.ui, {});
  const state = ref<UiState>({ ...defaults, ...saved });

  function persist(): void {
    writeJSON(LS.ui, state.value);
  }

  function rememberRecent(obj: Omit<RecentObject, 'at'>): void {
    state.value.recent = [
      { ...obj, at: Date.now() },
      ...state.value.recent.filter((r) => !(r.kind === obj.kind && r.id === obj.id)),
    ].slice(0, 10);
    persist();
  }

  function setLastProject(id: string | null): void {
    if (id) state.value.lastProjectId = id;
    persist();
  }

  function setFilters(scope: string, value: unknown): void {
    state.value.filters[scope] = value;
    persist();
  }

  function getFilters<T>(scope: string, fallback: T): T {
    return (state.value.filters[scope] as T) ?? fallback;
  }

  /** Left-swipe a todo card to hide it locally for 24 hours (UM-HOME-002). */
  function snooze(key: string): void {
    state.value.snoozed[key] = Date.now() + 24 * 3600_000;
    persist();
  }

  function isSnoozed(key: string): boolean {
    const until = state.value.snoozed[key];
    return !!until && until > Date.now();
  }

  function setDataClass(projectId: string, value: string): void {
    state.value.dataClassByProject[projectId] = value;
    persist();
  }

  function getDataClass(projectId: string): string | undefined {
    return state.value.dataClassByProject[projectId];
  }

  function clearLocal(): void {
    state.value = { ...defaults };
    persist();
  }

  return { state, rememberRecent, setLastProject, setFilters, getFilters, snooze, isSnoozed, setDataClass, getDataClass, clearLocal };
});
