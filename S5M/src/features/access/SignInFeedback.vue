<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useAuthStore } from '@/stores/auth';
import { translateErrorCode } from '@/i18n';
import FuiSpinner from '@/fui/FuiSpinner.vue';

// What a sign-in screen owes the operator between the tap and the next screen: that something is
// happening, why it did not work, and who they are when it did (UM-AUTH-001).
//
// This is a component rather than two copies because there are two entry points — the shell's dialog
// and the card on 权限管理 — and a reason worded differently in each is a support ticket waiting to
// happen. It reads the store directly: the failure is session state, not field state.

defineProps<{ busy?: boolean; hint?: string }>();

const { t } = useI18n();
const auth = useAuthStore();

const reason = computed(() => {
  const failure = auth.signInFailure;
  if (!failure) return '';
  return translateErrorCode(failure.code, failure.message);
});

// The lock note is identical for every refusal, including the ones that are not about a lock at all.
// S1 answers "no such account" and "wrong password" with the same words on purpose, and a console
// that guessed louder than that would become the enumeration channel the server refused to be
// (CP-IAM-005 ④) — so this is a rule stated once, not a diagnosis of this particular answer.
const lockNote = computed(() => (reason.value ? t('auth.lockNote') : ''));
</script>

<template>
  <div v-if="busy || hint || reason" class="sif" role="status" aria-live="polite">
    <span v-if="busy" class="sif__busy"><FuiSpinner size="16" /> {{ t('auth.signInBusy') }}</span>
    <span v-else-if="hint" class="sif__hint">{{ hint }}</span>
    <template v-else-if="reason">
      <span class="sif__fail">{{ reason }}</span>
      <span v-if="auth.signInFailure?.correlationId" class="sif__cid">{{ auth.signInFailure.correlationId }}</span>
      <span class="sif__note">{{ lockNote }}</span>
    </template>
  </div>
</template>

<style scoped>
.sif { display: flex; flex-direction: column; gap: 4px; padding: 6px 0; font-size: var(--fui-fs-sm); }
.sif__busy, .sif__hint { display: flex; gap: 6px; align-items: center; color: var(--fui-text-secondary); }
.sif__fail { color: var(--fui-danger); font-weight: 600; }
.sif__cid { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); word-break: break-all; }
.sif__note { color: var(--fui-text-secondary); }
</style>
