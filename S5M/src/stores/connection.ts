import { defineStore } from 'pinia';
import { ref } from 'vue';

// Ephemeral — never persisted. Reflects the live transport only (UM-NET-003/004/005).
export type ConnStatus = 'idle' | 'connecting' | 'open' | 'polling' | 'offline';

export const useConnectionStore = defineStore('connection', () => {
  const status = ref<ConnStatus>('idle');
  /** Timestamp of the last successful data load, shown by the offline banner (UM-NET-005). */
  const lastLoadedAt = ref<number>(Date.now());

  function set(s: ConnStatus): void {
    status.value = s;
  }

  function markLoaded(): void {
    lastLoadedAt.value = Date.now();
  }

  return { status, lastLoadedAt, set, markLoaded };
});
