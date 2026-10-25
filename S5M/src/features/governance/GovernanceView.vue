<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute, useRouter } from 'vue-router';
import { FuiCard, FuiStatusTag, FuiAsync, FuiActionButton, FuiBottomSheet, FuiField, FuiCell, FuiSegmented, FuiCheckChips } from '@/fui';
import ProviderSheet from './ProviderSheet.vue';
import ModelSheet from './ModelSheet.vue';
import { useGovernanceAgents, useGovernanceBudget, useGovernanceEvaluations, useGovernanceModels, useGovernancePolicy, useGovernancePrompts, useGovernanceProviders, useGovernanceUsage, useProjects, useProviderCatalogue, useRegressions } from '@/api/queries';
import { useRelaxPolicy, useActivatePrompt, useSetBudget, useUpdateProvider, useUpdateModel, useTestProvider } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { PROVIDER_CLASSES } from '@/lib/enums';
import { budgetStateLabel, providerClassLabel, stageTypeLabel } from '@/lib/labels';
import { pushSuccess } from '@/lib/toast';
import type * as T from '@/api/types';

const { t, te } = useI18n();
const route = useRoute();
const router = useRouter();

/**
 * Which half of the governance surface is on screen. The URL is the only state: a section can then
 * be linked from the guided setup, from search and from the 我的 page, and 后退 lands where the
 * operator came from instead of at the top of a page they had scrolled past.
 */
const SECTION_KEYS = ['models', 'providers', 'policy', 'prompts', 'cost', 'evaluations'];
const section = computed(() => {
  const wanted = String(route.params.section ?? 'models');
  return SECTION_KEYS.includes(wanted) ? wanted : 'models';
});
const sectionOptions = computed(() => SECTION_KEYS.map((key) => ({ value: key, label: t(`governance.section.${key}`) })));
function goSection(value: string): void {
  void router.push(`/governance/${value}`);
}

const models = useGovernanceModels();
const policy = useGovernancePolicy();
const providers = useGovernanceProviders();
const usage = useGovernanceUsage();
const prompts = useGovernancePrompts();
const evaluations = useGovernanceEvaluations();
const agents = useGovernanceAgents();
const relax = useRelaxPolicy();
const activate = useActivatePrompt();

// budget is per-project (projectId query required by S1); empty = platform default
const projects = useProjects(false);
const budgetScope = ref('');
const budgetScopeOpen = ref(false);
const budget = useGovernanceBudget(budgetScope);
const setBudget = useSetBudget();
const budgetForm = reactive({ limit: '', thresholds: '', currency: 'USD' });
function submitBudget(): void {
  const thresholds = budgetForm.thresholds.split(',').map((s) => parseFloat(s.trim())).filter((n) => !Number.isNaN(n));
  setBudget.mutate(
    { projectId: budgetScope.value || undefined, body: { limit: Number(budgetForm.limit) || 0, currency: budgetForm.currency, thresholds } },
    { onSuccess: () => pushSuccess(t('budget.saved')) },
  );
}

// S1 answers the list envelope, and `ModelList.items` is the typed row the edit sheet needs —
// handing the sheet a `Record<string, unknown>` would have meant re-reading the row's fields off
// the wire at the other end, where a renamed column fails silently instead of failing the typecheck.
const modelList = computed<T.ModelView[]>(() => models.data.value?.items ?? []);
const promptList = computed<Record<string, unknown>[]>(() => (prompts.data.value as unknown as { items?: Record<string, unknown>[] })?.items ?? []);
const evalList = computed<Record<string, unknown>[]>(() => (evaluations.data.value as unknown as { items?: Record<string, unknown>[] })?.items ?? []);
const agentList = computed<Record<string, unknown>[]>(() => (agents.data.value as unknown as { items?: Record<string, unknown>[] })?.items ?? (Array.isArray(agents.data.value) ? agents.data.value as unknown as Record<string, unknown>[] : []));
// S1 sends the list envelope; reading it as a bare array made this whole card render nothing.
const providerList = computed<T.ProviderView[]>(() => providers.data.value?.items ?? []);
/**
 * The stored row carries a preset key; the label lives in the same catalogue the form used.
 * An unknown key (a preset removed after the row was written) still reads as itself.
 */
const catalogue = useProviderCatalogue();
function vendorLabel(key?: string | null): string {
  if (!key || key === 'custom') return t('governance.vendorCustom');
  return String(catalogue.data.value?.items?.find((row) => String(row.key) === key)?.displayName ?? key);
}

// The form itself lives in `ModelSheet` so the guided step opens the same one; the table's own
// enable switch stays here.
const modelSheet = ref(false);
/** the row being edited, or null when the sheet opens to register a new model */
const modelTarget = ref<T.ModelView | null>(null);
const updateModel = useUpdateModel();

function openModel(model: T.ModelView | null = null): void {
  modelTarget.value = model;
  modelSheet.value = true;
}

function refetchModels(): void {
  void models.refetch();
}


/**
 * `applicableAgents` is a map from agent to what that agent may ask of the model — S3's own shape,
 * forwarded whole. Reading it as an array would call `.join` on an object and take the card down.
 *
 * Ordered by priority the way the runtime walks it (lower first), and named the way the registration
 * sheet names the same agent, so the card and the form describe one thing. An empty map means the
 * model is registered but claimed by nothing, which is the state an operator needs to see.
 */
function agentSummary(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return Array.isArray(value) ? value.join(' · ') : '';
  return Object.entries(value as Record<string, unknown>)
    .map(([agent, item]) => ({ agent, order: Number(Array.isArray(item) ? NaN : item), text: Array.isArray(item) ? `${agentLabel(agent)} ${item.join(', ')}` : `${agentLabel(agent)} ${String(item)}` }))
    .sort((a, b) => a.order - b.order || a.agent.localeCompare(b.agent))
    .map((row) => row.text)
    .join(' · ');
}

/** An agent type stored before this build knew its name still reads as itself. */
function agentLabel(type: string): string {
  const key = `agents.${type}`;
  return te(key) ? t(key) : type;
}

/**
 * The four outbound policies are the provider classes under another name, and the labels for
 * them are already in the governance namespace — one map, checked against the contract's own
 * union so a renamed value fails the typecheck instead of printing a raw token.
 */
const OUTBOUND_LABEL: Record<(typeof PROVIDER_CLASSES)[number], string> = {
  LOCAL_ONLY: 'environments.policyLocal', PRIVATE_NETWORK: 'environments.policyPrivate',
  APPROVED_EXTERNAL: 'environments.policyApproved', UNRESTRICTED: 'environments.policyUnrestricted',
};

function outboundLabel(value: string): string {
  return value in OUTBOUND_LABEL ? t(OUTBOUND_LABEL[value as keyof typeof OUTBOUND_LABEL]) : value;
}

/** The classification matrix S1 answers with, in the order the server sent it. */
const policyRows = computed<[string, string[]][]>(() =>
  Object.entries((policy.data.value?.effective?.allowed ?? {}) as Record<string, string[]>),
);

/** A stored provider whose class is not in the contract shows the raw token, never a wrong label. */
// The map lives once, in `lib/labels.ts`, so the table and the registration sheet cannot disagree
// about what a stored `providerClass` means.
function classLabel(value?: string | null): string {
  return value ? providerClassLabel(value) : '—';
}

/** Switching a provider off names nothing else, which is exactly why it is safe to offer here. */
const providerSheet = ref(false);
const providerTarget = ref<T.ProviderView | null>(null);
/** The register/edit form lives in `ProviderSheet`, so the guided setup opens the same one; the
    enable switch below stays here because it belongs to the table, not to the form. */
const editProvider = useUpdateProvider();

function openProvider(provider: T.ProviderView | null): void {
  providerTarget.value = provider;
  providerSheet.value = true;
}

/** The sheet owns the write; the page owns what its own tables re-read. */
function refetchProviders(): void {
  void providers.refetch();
}

function toggleProvider(p: T.ProviderView): void {
  editProvider.mutate(
    { name: p.name ?? '', body: { enabled: !p.enabled } },
    { onSuccess: () => pushSuccess(t('governance.providerSaved')) },
  );
}

/**
 * S5IF-GOV20. One probe's answer, kept against the provider it was asked about.
 *
 * This exists because a parse failure reads `model endpoint unreachable: ConnectError` whether the
 * address is wrong, the key is wrong, or the vendor is simply slower than the request timeout — and
 * those need three different fixes. Asking the endpoint directly is the only way to tell them apart
 * without reading the model-call table, which no screen shows.
 */
const probe = useTestProvider();
const probeFor = ref('');
const probeAnswer = ref<{ name: string; healthy: boolean; detail: string } | null>(null);

function probeProvider(p: T.ProviderView): void {
  const name = p.name ?? '';
  if (!name) return;
  probeFor.value = name;
  probe.mutate(
    { name },
    {
      onSuccess: (res) => {
        const view = (res ?? {}) as Record<string, unknown>;
        probeAnswer.value = {
          name,
          healthy: Boolean(view.healthy),
          detail: String(view.detail ?? ''),
        };
      },
      onError: () => {
        probeAnswer.value = { name, healthy: false, detail: t('governance.probeFailed') };
      },
    },
  );
}

const relaxSheet = ref(false);
const relaxReason = ref('');
/**
 * UI-ADM-003. The offerable classes are a subtraction — `ceiling[level]` minus
 * `allowed[level]` — computed from what S1 answered, never from a list typed in here. A level
 * whose difference is empty has nothing to ask for (PHI_PII always does: AC-POL-02), and
 * offering it anyway would invite the 403 S3 reserves for a request past the ceiling.
 */
const allowedByLevel = computed<Record<string, string[]>>(
  () => (policy.data.value?.effective?.allowed ?? {}) as Record<string, string[]>,
);
const ceilingByLevel = computed<Record<string, string[]>>(
  () => (policy.data.value?.ceiling?.allowed ?? {}) as Record<string, string[]>,
);
const relaxLevels = computed(() => Object.keys(allowedByLevel.value));
const relaxLevelOptions = computed(() =>
  relaxLevels.value.map((name) => ({ value: name, label: t(`governance.classification${name}`, { defaultValue: name }) })),
);
const relaxLevel = ref('');
const relaxPicked = ref<string[]>([]);
const relaxCandidates = computed(() => {
  const level = relaxLevel.value;
  return (ceilingByLevel.value[level] ?? []).filter((value) => !(allowedByLevel.value[level] ?? []).includes(value));
});
const relaxClassOptions = computed(() => relaxCandidates.value.map((value) => ({ value, label: outboundLabel(value) })));
function openRelax(): void {
  relaxLevel.value =
    relaxLevels.value.find((name) => (ceilingByLevel.value[name]?.length ?? 0) > (allowedByLevel.value[name]?.length ?? 0)) ??
    relaxLevels.value[0] ??
    '';
  relaxPicked.value = [];
  relaxReason.value = '';
  relaxSheet.value = true;
}
// A level's candidate list is computed from it, so picks made under the previous level are dropped
// rather than carried into a set they may no longer belong to.
function pickRelaxLevel(level: string): void {
  relaxLevel.value = level;
  relaxPicked.value = [];
}
const relaxProblems = computed<string[]>(() => {
  const out = new Set<string>();
  if (!relaxLevel.value) out.add(t('governance.relaxLevelRequired'));
  if (!relaxCandidates.value.length) {
    out.add(relaxLevel.value === 'PHI_PII' ? t('governance.relaxPhiNever') : t('governance.relaxCeilingReached'));
  }
  if (!relaxPicked.value.length) out.add(t('governance.relaxClassesRequired'));
  if (!relaxReason.value.trim()) out.add(t('governance.relaxReasonMissing'));
  return [...out];
});
function doRelax(): void {
  if (relaxProblems.value.length) return;
  relax.mutate(
    {
      change: { level: relaxLevel.value, addClasses: [...relaxPicked.value] },
      reason: relaxReason.value.trim(),
      scope: 'organization',
    },
    {
      onSuccess: () => {
        relaxSheet.value = false;
        pushSuccess(t('governance.relaxGranted'));
      },
    },
  );
}
const regressions = useRegressions();

/**
 * UI-ADM-003's precondition, computed from what S1 reports.
 *
 * Both lists are read once and matched here; asking per version would be one request per row.
 * Evaluations quote a prompt as a free string (`name.vN` today), regressions by `promptVersionId`,
 * so both spellings are accepted rather than bet on. A failed evaluation is not coverage: showing
 * it as coverage would let a version activate on evidence that says the opposite.
 */
function coverage(p: Record<string, unknown>): { ready: boolean; missing: string } {
  const name = String(p.name ?? '');
  const version = String(p.version ?? '');
  const dotted = `${name}.v${version}`;
  const at = `${name}@${version}`;
  const evalRows = (evaluations.data.value?.items ?? []) as unknown as Record<string, unknown>[];
  const regRows = (regressions.data.value?.items ?? []) as unknown as Record<string, unknown>[];
  const hasEvaluation = evalRows.some((e) => {
    const quoted = String(e.prompt ?? '');
    return (e.passed === undefined || e.passed === true) && (quoted.includes(dotted) || quoted.includes(at));
  });
  const hasRegression = regRows.some((r) => r.passed !== false && String(r.promptVersionId ?? '') === dotted);
  const missing = [
    ...(hasEvaluation ? [] : [t('governance.needEvaluation')]),
    ...(hasRegression ? [] : [t('governance.needRegression')]),
  ];
  return { ready: missing.length === 0, missing: missing.join(' · ') };
}

function doActivate(p: Record<string, unknown>): void {
  activate.mutate({ name: p.name, version: p.version, qualifiedName: p.qualifiedName });
}
</script>

<template>
  <div class="gv">
    <!-- one section on screen at a time, and the URL says which: this page used to be eight
         cards deep with nothing to navigate them but scrolling (UM-NAV-001) -->
    <FuiSegmented :model-value="section" :options="sectionOptions" :label="t('governance.title')" @update:model-value="goSection" />
    <template v-if="section === 'models'">
    <FuiCard :title="t('governance.models')">
      <FuiActionButton
        platform
        size="small"
        :action="ACTION.governanceModelRegister"
        :label="t('governance.registerModel')"
        @click="openModel()"
      />
      <FuiAsync
        :loading="models.isLoading.value"
        :empty="!modelList.length"
        :empty-title="t('governance.modelsEmptyTitle')"
        :empty-hint="t('governance.modelsEmptyHint')"
      >
        <div v-for="m in modelList" :key="String(m.ref)" class="gv__model">
          <div class="gv__mrow"><b>{{ m.ref }}</b><FuiStatusTag :tone="m.enabled ? 'success' : 'muted'" :label="m.enabled ? t('governance.active') : '—'" /></div>
          <div class="gv__mmeta">{{ m.provider }} · {{ m.endpoint }} · {{ agentSummary(m.applicableAgents) || t('governance.noAgentsYet') }}</div>
          <div class="gv__secret">{{ m.apiKeySecretRef ?? '' }}</div>
          <div class="gv__actions">
            <!-- Editing is the same verb as the switch below (S5IF-GOV06 is one PATCH whose body
                 names only what changed), so it is gated by the same action the guard consults. -->
            <FuiActionButton
              platform
              size="mini"
              :action="ACTION.governanceModelEnable"
              :label="t('governance.editModel')"
              @click="openModel(m)"
            />
            <FuiActionButton
              platform
              size="mini"
              :variant="m.enabled ? 'danger' : 'success'"
              :action="ACTION.governanceModelEnable"
              :label="m.enabled ? t('governance.disableModel') : t('governance.enableModel')"
              :loading="updateModel.isPending.value"
              @click="updateModel.mutate({ ref: String(m.ref), body: { enabled: !m.enabled } })"
            />
          </div>
        </div>
      </FuiAsync>
    </FuiCard>
    </template>

    <template v-if="section === 'policy'">
    <FuiCard :title="t('governance.policy')">
      <!-- the matrix S1 actually answers with, rather than a JSON dump: one line per data
           classification, naming the outbound policies that classification may use -->
      <div v-for="[classification, allowed] in policyRows" :key="classification" class="gv__prow">
        <b>{{ t(`governance.classification${classification}`, { defaultValue: classification }) }}</b>
        <span>{{ allowed.length ? allowed.map(outboundLabel).join(' · ') : t('governance.nothingAllowed') }}</span>
      </div>
      <FuiActionButton :action="ACTION.governancePolicyRelax" platform size="small" :label="t('governance.relax')" @click="openRelax" />
    </FuiCard>
    </template>

    <!-- UI-ADM-003: a version may only be made effective once its evaluation and its Golden
         Project regression result are on record, and the reason has to be readable before the tap. -->
    <template v-if="section === 'prompts'">
    <FuiCard :title="t('governance.prompts')">
      <FuiAsync
        :loading="prompts.isLoading.value"
        :error="prompts.isError.value || evaluations.isError.value || regressions.isError.value"
        :empty="!promptList.length"
        :empty-title="t('governance.prompts')"
        @retry="() => { prompts.refetch(); evaluations.refetch(); regressions.refetch(); }"
      >
        <div v-for="p in promptList" :key="String(p.qualifiedName)" class="gv__prompt">
          <div class="gv__mrow"><b>{{ p.name }}</b> · v{{ p.version }}<FuiStatusTag v-if="p.active" tone="success" :label="t('governance.active')" /></div>
          <div class="gv__mmeta">{{ agentLabel(String(p.agentType ?? '')) }}</div>
          <FuiActionButton
            :action="ACTION.governancePromptActivate"
            platform
            size="mini"
            :label="t('governance.activate')"
            :disabled="!!p.active || !coverage(p).ready"
            @click="doActivate(p)"
          />
          <span v-if="!p.active && !coverage(p).ready" class="gv__why">{{ coverage(p).missing }}</span>
        </div>
      </FuiAsync>
    </FuiCard>
    </template>

    <!-- UI-ADM-005: budget is readable and writable; S1 answers PUT /governance/budget -->
    <template v-if="section === 'cost'">
    <FuiCard :title="t('governance.budget')">
      <button class="gv__scope" @click="budgetScopeOpen = true">{{ budgetScope || t('budget.platformScope') }}</button>
      <div v-if="budget.data.value" class="gv__budget">
        <span>{{ t('governance.usage') }}: {{ budget.data.value.spent }} {{ budget.data.value.currency }}</span>
        <span> · limit: {{ budget.data.value.limit ?? '—' }}</span>
        <FuiStatusTag :tone="budget.data.value.state === 'OK' ? 'success' : 'warning'" :label="budgetStateLabel(budget.data.value.state)" />
      </div>
      <p class="gv__soon">{{ t('budget.scopeHint') }}</p>
      <FuiField v-model="budgetForm.limit" type="number" :label="t('budget.limit')" />
      <FuiField v-model="budgetForm.thresholds" :label="t('budget.thresholds')" />
      <FuiField v-model="budgetForm.currency" :label="t('budget.currency')" />
      <FuiActionButton
        platform
        :action="ACTION.governanceBudgetSet"
        variant="primary"
        block
        size="small"
        :loading="setBudget.isPending.value"
        :label="t('budget.edit')"
        @click="submitBudget"
      />
    </FuiCard>

    <FuiCard :title="t('governance.usage')">
      <pre class="gv__pre" role="region" tabindex="0" :aria-label="t('governance.usage')">{{ JSON.stringify(usage.data.value, null, 2) }}</pre>
    </FuiCard>
    </template>

    <!-- M3: evaluations -->
    <template v-if="section === 'evaluations'">
    <FuiCard :title="t('governance.evaluations')">
      <FuiAsync :loading="evaluations.isLoading.value" :empty="!evalList.length" :empty-title="t('governance.evaluations')">
        <div v-for="ev in evalList" :key="String(ev.id)" class="gv__eval">
          <div class="gv__mrow"><b>{{ ev.model }}</b><FuiStatusTag :tone="ev.passed ? 'success' : 'danger'" :label="`${ev.score ?? ''} · ${ev.passed ? t('governance.evalPass') : t('governance.evalFail')}`" /></div>
          <div class="gv__mmeta">{{ ev.agentType }} · {{ ev.prompt }} · cases {{ ev.caseCount }}</div>
        </div>
      </FuiAsync>
      <!-- 这句讲的是评测本身。借用审计页「提交追溯」的文案会让读的人以为评测没跑，而实际只是明细在桌面。 -->
      <p class="gv__soon">{{ t('governance.evalsDetailSoon') }}</p>
    </FuiCard>
    </template>

    <template v-if="section === 'providers'">
    <FuiCard :title="t('governance.providers')">
      <FuiActionButton :action="ACTION.governanceProviderRegister" platform size="small" :label="t('governance.registerProvider')" @click="openProvider(null)" />
      <p v-if="!providerList.length" class="gv__soon">{{ t('governance.providersEmpty') }}</p>
      <div v-for="p in providerList" :key="String(p.name)" class="gv__provrow">
        <div class="gv__mrow">
          <b>{{ p.name }}</b>
          <FuiStatusTag :tone="p.enabled ? 'success' : 'muted'" :label="p.enabled ? t('governance.enable') : t('governance.providerDisable')" />
        </div>
        <div class="gv__mmeta">{{ vendorLabel(p.vendor) }} · {{ classLabel(p.providerClass) }} · {{ p.endpoint }} · {{ p.adapter }}</div>
        <div class="gv__secret">{{ p.apiKeySecretRef ?? '' }}</div>
        <div v-if="p.suggestedModels?.length" class="gv__mmeta">{{ t('governance.suggestedModelsCount', { count: p.suggestedModels.length }) }}</div>
        <div class="gv__acts">
          <FuiActionButton :action="ACTION.governanceProviderEnable" platform size="mini" :label="t('governance.editProvider')" @click="openProvider(p)" />
          <FuiActionButton :action="ACTION.governanceProviderEnable" platform size="mini" :label="p.enabled ? t('governance.providerDisable') : t('governance.enable')" @click="toggleProvider(p)" />
          <FuiActionButton :action="ACTION.governanceProviderEnable" platform size="mini" :label="t('governance.probe')" :loading="probeFor === p.name && probe.isPending.value" @click="probeProvider(p)" />
        </div>
        <p
          v-if="probeAnswer && probeAnswer.name === p.name"
          class="gv__probe"
          :class="probeAnswer.healthy ? 'gv__probe--ok' : 'gv__probe--bad'"
          role="status"
        >
          {{ probeAnswer.healthy ? t('governance.probeOk') : t('governance.probeBad') }}<span v-if="probeAnswer.detail"> — {{ probeAnswer.detail }}</span>
        </p>
      </div>
    </FuiCard>
    </template>

    <template v-if="section === 'models'">
    <FuiCard v-if="agentList.length" :title="t('governance.agentDirectory')">
      <!-- the agents a model can be named for, in the words the model form uses; the card used to
      carry the same heading as the one above it and print the raw row -->
      <div v-for="ag in agentList" :key="String(ag.agentType)" class="gv__prow">
        <b>{{ agentLabel(String(ag.agentType ?? '')) }}</b>
        <span>{{ stageTypeLabel(String(ag.stage ?? '')) }}</span>
        <FuiStatusTag v-if="ag.enabled !== true" tone="muted" :label="t('governance.agentOff')" />
      </div>
    </FuiCard>
    </template>

    <FuiBottomSheet v-model="relaxSheet" :title="t('governance.relaxTitle')">
      <!-- D-07 / AI-POL-002: this is a permanent, organisation-wide change with an audit record,
           and until M4 the only identity check is the operator's name. The sheet says so rather
           than letting a tap read like a small local setting. -->
      <p class="gv__warn">{{ t('governance.relaxPermanentBody') }}</p>
      <div class="gv__group">
        <span class="gv__legend">{{ t('governance.relaxLevel') }}</span>
        <FuiSegmented :model-value="relaxLevel" :options="relaxLevelOptions" :label="t('governance.relaxLevel')" @update:model-value="pickRelaxLevel" />
      </div>
      <div class="gv__group">
        <span class="gv__legend">{{ t('governance.relaxClasses') }}</span>
        <FuiCheckChips v-model="relaxPicked" :options="relaxClassOptions" :label="t('governance.relaxClasses')" />
        <p v-if="!relaxCandidates.length" class="gv__err">
          {{ relaxLevel === 'PHI_PII' ? t('governance.relaxPhiNever') : t('governance.relaxCeilingReached') }}
        </p>
      </div>
      <FuiField v-model="relaxReason" type="textarea" rows="2" :label="t('governance.relaxReason')" :placeholder="t('common.required')" />
      <p v-for="p in relaxProblems" :key="p" class="gv__err">{{ p }}</p>
      <template #footer>
        <FuiActionButton
          platform
          :action="ACTION.governancePolicyRelax"
          variant="primary"
          block
          :label="t('common.confirm')"
          :loading="relax.isPending.value"
          :disabled="relaxProblems.length > 0"
          @click="doRelax"
        />
      </template>
    </FuiBottomSheet>

    <ModelSheet v-model="modelSheet" :model="modelTarget" @saved="refetchModels" />

    <ProviderSheet v-model="providerSheet" :provider="providerTarget" @saved="refetchProviders" />

    <FuiBottomSheet v-model="budgetScopeOpen" :title="t('projects.title')">
      <FuiCell :title="t('budget.platformScope')" is-link @click="budgetScope = ''; budgetScopeOpen = false" />
      <FuiCell v-for="p in projects.data.value?.items ?? []" :key="p.id" :title="p.name" is-link @click="budgetScope = p.id; budgetScopeOpen = false" />
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.gv { padding: var(--fui-sp-4); }
/* a native select: labelled, keyboard-operable, works at every font scale (UM-NFR-03) */
.gv__pick { min-height: var(--fui-tap-min); width: 100%; font-size: var(--fui-fs-sm); color: var(--fui-text); background: var(--fui-surface); border: 1px solid var(--fui-border); border-radius: var(--fui-radius); padding: 0 var(--fui-sp-2); }
.gv__model, .gv__prompt, .gv__eval { padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.gv__mrow { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.gv__mmeta { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin: 4px 0; }
.gv__actions { display: flex; flex-wrap: wrap; gap: var(--fui-sp-1); }
.gv__secret { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.gv__probe { font-size: var(--fui-fs-xs); margin: 6px 0 0; word-break: break-word; }
.gv__probe--ok { color: var(--fui-success, var(--fui-primary)); }
.gv__probe--bad { color: var(--fui-danger); }
.gv__pre { white-space: pre-wrap; font-size: var(--fui-fs-xs); overflow: auto; max-height: 30vh; background: var(--fui-surface-2); padding: 8px; border-radius: 8px; }
.gv__soon { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 8px 0 0; }
.gv__prov, .gv__provrow { padding: 6px 0; font-size: var(--fui-fs-sm); display: flex; flex-direction: column; gap: 4px; border-bottom: 1px solid var(--fui-border); }
.gv__acts { display: flex; gap: 8px; margin-top: 4px; }
.gv__err { color: var(--fui-danger); font-size: var(--fui-fs-xs); margin: 4px 0; }
.gv__group { margin: 10px 0; }
.gv__legend { display: block; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin-bottom: 4px; }
.gv__warn { color: var(--fui-warning); font-size: var(--fui-fs-xs); margin: 6px 0; }
/* one line per data classification: the level, then what it may send */
.gv__prow { display: flex; flex-wrap: wrap; gap: 6px; justify-content: space-between; font-size: var(--fui-fs-xs); padding: 4px 0; border-bottom: 1px solid var(--fui-border); }
.gv__budget { display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-sm); flex-wrap: wrap; margin-bottom: 8px; }
.gv__scope { width: 100%; text-align: left; min-height: var(--fui-tap-min); border: 1px dashed var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); color: var(--fui-primary); padding: 0 12px; margin-bottom: 8px; }
</style>
