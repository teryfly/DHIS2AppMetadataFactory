import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { LS, readJSON, writeJSON } from '@/lib/storage';
import { useAuthStore } from '@/stores/auth';

// Operator identity. MVP: user-typed name, persisted locally (UM-ME-001). All state-changing
// requests attach it as X-Operator; without it, every mutating action is disabled (UM-AC-01).
export const useOperatorStore = defineStore('operator', () => {
  const auth = useAuthStore();
  const name = ref<string>(readJSON<string>(LS.operator, ''));
  // With a session there is nothing to type: S1 overwrites the operator with the account behind
  // the cookie and ignores X-Operator outright (rest/middleware.py), so a missing name here must
  // not read as "no identity" and grey out every action.
  const hasOperator = computed(() => auth.authenticated || name.value.trim().length > 0);
  // Who "我" is for a field that records a person (a project's 负责人): the account when there is
  // a session, the locally typed operator otherwise.
  const display = computed(() => auth.session?.displayName || auth.session?.subject || name.value);

  function setName(v: string): void {
    name.value = v.trim();
    writeJSON(LS.operator, name.value);
  }

  function clear(): void {
    name.value = '';
    writeJSON(LS.operator, '');
  }

  return { name, display, hasOperator, setName, clear };
});
