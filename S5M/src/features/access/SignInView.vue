<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import FuiButton from '@/fui/FuiButton.vue';
import FuiCard from '@/fui/FuiCard.vue';
import FuiField from '@/fui/FuiField.vue';
import FuiNotice from '@/fui/FuiNotice.vue';
import { useAuthStore } from '@/stores/auth';
import { translateErrorCode } from '@/i18n';
import { safeReturnTo } from '@/lib/origin';
import { pushSuccess } from '@/lib/toast';
import SignInFeedback from './SignInFeedback.vue';

// The sign-in page (UM-AUTH-001, UM-NET-009).
//
// It used to be a modal over whatever screen the operator was already on. A page of its own is both
// simpler to read on a phone and honest about what happened: the shell brings you here when there is
// no session, the screen says why, and there is nowhere for a half-finished form to hide.
//
// Two things this screen must not do. It must not invent a reason the server did not give — S1 answers
// "no such account" and "wrong password" identically, and `SignInFeedback` states the one rule that
// applies to every refusal (CP-IAM-005 ④). And it must not hand the operator off anywhere but back
// into this app: `next` is a path, validated below, never a URL.

const { t } = useI18n();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

const form = reactive({ subject: '', password: '' });
const busy = ref(false);
const hint = ref('');

// Only the local nudge is cleared on an edit. The refusal itself stays on screen until the next
// attempt: `login()` clears it when it starts, and clearing it here would erase the reason the
// moment the form wipes the rejected password out of the field — which is the operator's own
// mistake to fix, with the explanation gone.
watch([() => form.subject, () => form.password], () => { hint.value = ''; });

/** The reason the organisation's door did not open, in the words S1 used (UM-AUTH-001). */
const oidcReason = computed(() => {
  const failure = auth.oidcFailure;
  return failure ? translateErrorCode(failure.code, failure.message) : '';
});

/** Where a successful sign-in continues to. Only an in-app path; anything else is dropped. */
const returnTo = computed(() => safeReturnTo(typeof route.query.next === 'string' ? route.query.next : ''));

onMounted(() => {
  // Someone who is already signed in has no business on this screen — the shell sends them away too,
  // but a bookmark or a back-navigation lands here directly.
  if (auth.authenticated) void router.replace(returnTo.value);
});

async function submit(): Promise<void> {
  if (!form.subject.trim() || !form.password) {
    hint.value = t('auth.needBoth');
    return;
  }
  busy.value = true;
  try {
    await auth.login(form.subject.trim(), form.password);
    form.password = '';
    pushSuccess(t('auth.signedInAs', { name: auth.session?.displayName || auth.session?.subject || form.subject }));
    void router.replace(returnTo.value);
  } catch {
    // The reason is painted under the fields it is about; the secret stays not-on-screen.
    form.password = '';
  } finally {
    busy.value = false;
  }
}

/**
 * The organisation's own sign-in page, one redirect away (UM-AUTH-001). This screen does not learn
 * what happened: the browser leaves and comes back at `/auth/callback`, which is where the `code`
 * is traded for a session. Same rule as the password form about what it keeps — nothing typed here
 * could be sent on, because there is nothing to type.
 */
async function signInWithOrganisation(): Promise<void> {
  hint.value = '';
  await auth.startOidc(returnTo.value);
}
</script>

<template>
  <div class="signin">
    <FuiCard :title="t('auth.signInTitle')">
      <p class="signin__why">{{ t('auth.loginHint') }}</p>
      <!-- Why this screen appeared is part of the answer: being sent to sign-in while mid-task reads
           as a crash unless the page says the session ended. -->
      <p v-if="auth.sessionLapsed" class="signin__lapsed">{{ t('auth.sessionExpiredHint') }}</p>
      <FuiField v-model="form.subject" :label="t('auth.username')" :placeholder="t('auth.username')" />
      <FuiField v-model="form.password" type="password" :label="t('auth.password')" :placeholder="t('auth.password')" @keyup.enter="submit" />
      <SignInFeedback :busy="busy" :hint="hint" />
      <FuiButton variant="primary" block :label="t('auth.signIn')" :loading="busy" @click="submit" />
      <template v-if="auth.oidcAvailable">
        <div class="signin__or" aria-hidden="true"><span>{{ t('auth.or') }}</span></div>
        <FuiButton block :label="t('auth.signInWithOrganisation')" :loading="auth.oidcBusy" @click="signInWithOrganisation" />
        <FuiNotice v-if="oidcReason" tone="danger" :text="oidcReason" />
      </template>
    </FuiCard>
  </div>
</template>

<style scoped>
.signin { padding: var(--fui-sp-4); max-width: 420px; margin: 0 auto; }
.signin__why { margin: 0 0 var(--fui-sp-3); font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.signin__lapsed { margin: 0 0 var(--fui-sp-3); padding: 6px 8px; font-size: var(--fui-fs-sm); color: var(--fui-warning, #8a5a00); background: var(--fui-warn-bg, #fff7e6); border-radius: var(--fui-radius-btn); }
/* The two doors are alternatives, not a form and a footnote under it: a rule with the word in the
   middle is how a phone screen says "or" without a sentence. */
.signin__or { display: flex; align-items: center; gap: 8px; margin: var(--fui-sp-3) 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.signin__or::before, .signin__or::after { content: ''; flex: 1; height: 1px; background: var(--fui-border); }
</style>
