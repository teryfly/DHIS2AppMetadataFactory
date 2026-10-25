<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiButton, FuiNotice, FuiAsync } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { useSetup, usePlatformActions } from '@/api/queries';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { capabilityFacts, startSteps } from './startSteps';
// UM-SET-006: the three registration forms open from the step that needs them, in the same sheet
// the Administration pages use. Two copies of a form would be two definitions of a valid
// registration, and the first server rule to move would leave one of them wrong.
import ProviderSheet from '@/features/governance/ProviderSheet.vue';
import ModelSheet from '@/features/governance/ModelSheet.vue';
import EnvironmentFormSheet from '@/features/environments/EnvironmentFormSheet.vue';

// UM-SET-001 … 005: the path from a fresh install to a first committed deployment, drawn from
// `GET /setup` (facts) plus `allowed-actions` (availability). Nothing here is inferred locally.
const { t } = useI18n();
const router = useRouter();
const setup = useSetup();
const platform = usePlatformActions();

const createAvailability = computed(() =>
  resolveAvailability(indexActions(platform.data.value), ACTION.projectCreate),
);
// The same localisation the gated buttons use, so the checklist and the button cannot explain the
// same refusal differently (UM-BR-01).
const createReason = computed(() => {
  const code = createAvailability.value.reasonCode;
  return code ? t(`actionReason.${code}`) : createAvailability.value.unknown ? t('actionReason.unknown') : '';
});

const steps = computed(() =>
  startSteps(setup.data.value, { available: createAvailability.value.available, reason: createReason.value }),
);

/**
 * Where the operator is standing. The first step S1 has not marked ready, unless they tapped
 * another one — the guide opens on the work, not on a summary of it.
 */
const picked = ref<string | null>(null);
const current = computed(
  () => steps.value.find((step) => step.key === picked.value) ?? steps.value.find((step) => step.state !== 'pass') ?? steps.value[0],
);
/**
 * Which registration is open. It names the sheet, not the step: the model step needs a provider
 * first, and a model that points at a provider nobody registered is exactly the 422 the operator
 * would otherwise have to discover by hand.
 */
const sheet = ref<string | null>(null);
const providerOpen = computed({
  get: () => sheet.value === 'providers',
  set: (open: boolean) => {
    if (!open) sheet.value = null;
  },
});
const modelOpen = computed({
  get: () => sheet.value === 'models',
  set: (open: boolean) => {
    if (!open) sheet.value = null;
  },
});
const environmentOpen = computed({
  get: () => sheet.value === 'environments',
  set: (open: boolean) => {
    if (!open) sheet.value = null;
  },
});

const facts = computed(() => setup.data.value);
/** the deployment's capability ports, stated as a fact rather than asked as a task */
const capability = computed(() => capabilityFacts(setup.data.value));
const problems = computed(() => facts.value?.configuration?.problems ?? []);
const stateTone = (state: string) => (state === 'pass' ? t('start.statePass') : state === 'fail' ? t('start.stateFail') : t('start.statePending'));

/**
 * Which action a step's own button is gated by. Only the steps that open a form here have one: the
 * rest navigate, and a link needs no permission (UM-BR-01 governs what changes state, and the
 * refusal for those steps is already carried in the note — `project.create` above).
 */
const goAction = computed(() => {
  const key = current.value?.key;
  if (key === 'environments') return ACTION.environmentCreate;
  if (key === 'models') return ACTION.governanceModelRegister;
  return '';
});
const providerGoAction = ACTION.governanceProviderRegister;

/**
 * S1 said the credential store is switched off (`credentials.storeAvailable`). The two steps whose
 * forms ask for a key are exactly where that matters, and saying it before the operator types is
 * worth more than the 503 that would otherwise arrive afterwards — the value they just typed is
 * discarded either way, and they are left guessing whether it was saved.
 */
const storeUnavailable = computed(() => facts.value?.credentials !== null && facts.value?.credentials?.storeAvailable === false);
const storeWarningHere = computed(() => storeUnavailable.value && ['models', 'environments'].includes(current.value?.key ?? ''));

function choose(key: string): void {
  picked.value = key;
  sheet.value = null;
}

/**
 * Steps that are a form on this page open it; the rest take the operator to where the work happens.
 * `providers` is deliberately in the second group: those facts are S1's subsystem wiring — whether
 * S2/S3/S4 are real or stand-ins — which is a deployment choice no browser control can change.
 */
function advance(kind = ''): void {
  const step = current.value;
  if (!step) return;
  if (step.key === 'models') {
    sheet.value = kind === 'provider' ? 'providers' : 'models';
    return;
  }
  if (step.key === 'environments') {
    sheet.value = 'environments';
    return;
  }
  picked.value = null;
  router.push(step.to);
}

function closeSheet(): void {
  sheet.value = null;
  // the write invalidated `setup`, so re-deriving the steps lands the operator on what is next
  picked.value = null;
  void setup.refetch();
}
</script>

<template>
  <div class="start">
    <FuiAsync :loading="setup.isLoading.value" :error="setup.isError.value" @retry="setup.refetch()">
      <FuiNotice v-if="problems.length" tone="danger">
        <div class="start__problems">
          <strong>{{ t('start.problemsTitle') }}</strong>
          <ul>
            <!-- Verbatim: each sentence names the variable to change in the env file and never a
                 value, so there is nothing here to mask (S5IF-G09). -->
            <li v-for="line in problems" :key="line"><code>{{ line }}</code></li>
          </ul>
        </div>
      </FuiNotice>

      <FuiNotice v-if="facts?.identity?.pendingPasswordChange" tone="warning">
        {{ t('start.passwordBody') }}
      </FuiNotice>

      <nav class="start__rail" :aria-label="t('start.title')">
        <button
          v-for="(step, index) in steps"
          :key="step.key"
          class="start__chip"
          :class="{ 'is-on': step.key === current?.key }"
          :aria-current="step.key === current?.key ? 'step' : undefined"
          @click="choose(step.key)"
        >
          <span class="start__num">{{ index + 1 }}</span>
          <span>{{ t(`start.step.${step.key}`) }}</span>
          <span class="start__state">{{ stateTone(step.state) }}</span>
        </button>
      </nav>

      <FuiCard v-if="current" :title="t(`start.step.${current.key}`)" :subtitle="stateTone(current.state)">
        <p class="start__note">{{ t(current.noteKey, current.params) }}</p>
        <FuiNotice v-if="storeWarningHere" tone="warning">{{ t('start.storeOffHere') }}</FuiNotice>
        <!-- The opener is gated like any other control: a step that opens a form this operator may
             not save should say so here, not after they have filled it in. -->
        <FuiActionButton
          v-if="goAction"
          platform
          :action="goAction"
          variant="primary"
          block
          :label="t(`start.go.${current.key}`)"
          @click="advance()"
        />
        <FuiButton v-else variant="primary" block :label="t(`start.go.${current.key}`)" @click="advance()" />
        <!-- A model has to hang on a provider, so the step that registers one also opens the
             registration it depends on rather than leaving the operator to find the order. -->
        <FuiActionButton
          v-if="current.key === 'models'"
          platform
          :action="providerGoAction"
          block
          :label="t('start.go.provider')"
          @click="advance('provider')"
        />
        <p class="start__hint">{{ t('start.inlineHint') }}</p>
      </FuiCard>

      <FuiCard :title="t('start.pipelineTitle')">
        <p class="start__note">{{ t('start.pipelineBody') }}</p>
      </FuiCard>

      <FuiCard :title="t('start.step.providers')">
        <p class="start__note">{{ t(capability.ready ? 'start.providersReady' : 'start.providersFake', { mode: capability.mode, which: capability.which }) }}</p>
        <p class="start__note">{{ t('start.providersMode', { mode: capability.mode }) }}</p>
        <!-- nothing here is a task: `provider_mode` is chosen in the server's own env file, so the
             guide reports the answer and asks for none -->
      </FuiCard>

      <ProviderSheet v-if="sheet === 'providers'" v-model="providerOpen" :provider="null" @saved="closeSheet" />
      <ModelSheet v-if="sheet === 'models'" v-model="modelOpen" :model="null" @saved="closeSheet" />
      <EnvironmentFormSheet v-if="sheet === 'environments'" v-model="environmentOpen" :environment="null" @saved="closeSheet" />
    </FuiAsync>
  </div>
</template>

<style scoped>
.start {
  padding: var(--fui-sp-3) 0 40px;
}
.start__note {
  color: var(--fui-text-secondary);
  font-size: var(--fui-fs-sm);
  margin: 0 0 var(--fui-sp-3);
}
.start__hint {
  color: var(--fui-text-tertiary);
  font-size: var(--fui-fs-xs);
  margin: var(--fui-sp-2) 0 0;
}
/* one chip per step, the current one marked with aria-current="step": the order is the point, so
   this is a list of steps rather than a tab strip */
.start__rail { display: flex; gap: var(--fui-sp-1); overflow-x: auto; padding-bottom: var(--fui-sp-2); }
.start__chip {
  display: flex; align-items: center; gap: 6px; min-height: var(--fui-tap-min);
  padding: 0 var(--fui-sp-3); white-space: nowrap;
  border: 1px solid var(--fui-border); border-radius: var(--fui-radius);
  background: var(--fui-surface); color: var(--fui-text-secondary); font-size: var(--fui-fs-sm);
}
.start__chip.is-on { border-color: var(--fui-primary); color: var(--fui-primary); font-weight: 600; }
.start__num { opacity: 0.6; }
.start__state { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.start__problems ul {
  margin: var(--fui-sp-1) 0 0;
  padding-left: 18px;
}
.start__problems code {
  font-size: var(--fui-fs-xs);
  word-break: break-all;
}
</style>
