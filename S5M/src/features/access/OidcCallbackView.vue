<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import FuiButton from '@/fui/FuiButton.vue';
import FuiCard from '@/fui/FuiCard.vue';
import FuiNotice from '@/fui/FuiNotice.vue';
import FuiSpinner from '@/fui/FuiSpinner.vue';
import { useAuthStore } from '@/stores/auth';
import { translateErrorCode } from '@/i18n';
import { pushSuccess } from '@/lib/toast';

// UM-AUTH-001: the landing half of the single sign-on round trip.
//
// The browser comes back here with `code` and `state`; S1 holds the matching `state` on an HttpOnly
// cookie it minted when the operator left, so nothing on this screen has to remember or compare it —
// it is echoed, and the server decides. `error` is the organisation's own refusal ("the operator
// cancelled" is the common one) and is shown as what it is: an answer from the other side, not ours.
//
// The exchange is fired at most once per code, in the store rather than by this component: a code is
// a spent credential the second time, and a re-mount must not offer it again.

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const code = computed(() => (typeof route.query.code === 'string' ? route.query.code : ''));
const state = computed(() => (typeof route.query.state === 'string' ? route.query.state : ''));
const denied = computed(() => (typeof route.query.error === 'string' ? route.query.error : ''));

const checking = computed(() => !denied.value && !!code.value && !auth.authenticated && !auth.signInFailure);
const refusal = computed(() => {
  const failure = auth.signInFailure;
  return failure ? translateErrorCode(failure.code, failure.message) : '';
});

onMounted(() => {
  if (auth.authenticated) void router.replace(auth.takeOidcReturn());
  else if (code.value) {
    auth.exchangeOidc(code.value, state.value).then(() => {
      // S1 answered with a session: this is the sign-in the operator started on the last screen, so
      // it gets the same words and the same destination.
      if (auth.authenticated) {
        pushSuccess(t('auth.signedInAs', { name: auth.session?.displayName || auth.session?.subject || '' }));
        void router.replace(auth.takeOidcReturn());
      }
    });
  }
});

function backToSignIn(): void {
  void router.replace({ name: 'login' });
}
</script>

<template>
  <div class="cb">
    <FuiCard :title="t('auth.signInTitle')">
      <p v-if="checking" class="cb__check"><FuiSpinner size="16" /> {{ t('auth.checking') }}</p>
      <FuiNotice v-else-if="denied" tone="danger" :text="`${t('auth.signInFailed')}: ${denied}`" />
      <FuiNotice v-else-if="!code" tone="warning" :text="t('auth.callbackMissing')" />
      <FuiNotice v-else-if="refusal" tone="danger" :text="refusal" />
      <FuiButton v-if="denied || !code || refusal" block variant="primary" :label="t('auth.signIn')" @click="backToSignIn" />
    </FuiCard>
  </div>
</template>

<style scoped>
.cb { padding: var(--fui-sp-4); max-width: 420px; margin: 0 auto; }
.cb__check { display: flex; align-items: center; gap: 6px; margin: 0; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.cb .fui-button { margin-top: var(--fui-sp-3); }
</style>
