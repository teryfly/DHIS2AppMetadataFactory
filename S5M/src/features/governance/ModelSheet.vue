<script setup lang="ts">
/**
 * Register one model under an existing provider (UM-SET-005 / S5IF-GOV05).
 *
 * Extracted from the governance page so the guided setup can open the same form from its own step.
 * No address and no key reference here on purpose: the model borrows both from the provider row, so
 * a model that carried its own would go stale the first time the provider moved.
 *
 * Every box that the runtime reads is filled with the value the runtime itself falls back to, and
 * each one carries a question mark saying what the number changes. This form used to leave them
 * empty and describe the deal in one line of small print — "blank means not applicable" — and both
 * halves of that were a trap: a model no agent was named for is never tried by anything, and a
 * context window left blank reached the server as `0`, which fails the token cutoff on every call.
 * The sheet has to be fillable by someone who has not read the policy code.
 */
import { computed, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiActionButton, FuiBottomSheet, FuiButton, FuiCell, FuiCheckChips, FuiField, FuiSegmented, FuiSwitch } from '@/fui';
// by path, not through the barrel: the sheet tests stub `@/fui`, and a stubbed help toggle would
// test a component that does not exist
import FuiHelp from '@/fui/FuiHelp.vue';
import { useGovernanceAgents, useGovernanceProviders, useProviderCatalogue } from '@/api/queries';
import { useRegisterModel, useUpdateModel } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { pushSuccess } from '@/lib/toast';
import type * as T from '@/api/types';

const props = defineProps<{ model: T.ModelView | null }>();
const show = defineModel<boolean>({ required: true });
const emit = defineEmits<{ (e: 'saved'): void }>();

const { t, te } = useI18n();
const providers = useGovernanceProviders();
const agents = useGovernanceAgents();
const catalogue = useProviderCatalogue();
const registerModel = useRegisterModel();
const updateModel = useUpdateModel();

/** Editing an existing row, rather than registering one. */
const stored = computed(() => props.model);

/** What the runtime assumes when a registration says nothing; the sheet states it instead. */
const DEFAULT_CONTEXT = '8000';
const DEFAULT_PRIORITY = '100';

const form = reactive({ provider: '', model: '', structuredOutput: true, vision: false, maxContextTokens: DEFAULT_CONTEXT, priceIn: '0', priceOut: '0', enabled: true });
const providerList = computed(() => providers.data.value?.items ?? []);
// `name` is both the value S1 keys a provider by and the only text a row carries; the two are the
// same string, which is why the chips show it unchanged.
const providerOptions = computed(() =>
  providerList.value.map((p) => ({ value: String(p.name ?? ''), label: String(p.name ?? '') })),
);
const providerRow = computed(() =>
  providerList.value.find((row) => String(row.name ?? '') === form.provider),
);
/**
 * The provider row's own suggestions come first: they are what the operator saw and saved when
 * registering it, and they survive an edited address. A legacy row (stored before the field
 * existed) falls back to the catalogue entry its vendor or endpoint names. Free text stays the
 * primary input — a private gateway, fine-tune or renamed deployment is normal.
 */
const modelSuggestions = computed<string[]>(() => {
  const stored = providerRow.value?.suggestedModels ?? [];
  if (stored.length) return stored;
  const preset = (catalogue.data.value?.items ?? []).find(
    (row) =>
      (providerRow.value?.vendor &&
        providerRow.value.vendor !== 'custom' &&
        String(row.key) === providerRow.value.vendor) ||
      (row.endpoint && row.endpoint === providerRow.value?.endpoint),
  );
  return (preset?.models ?? []).map(String);
});
const agentTypes = computed(() =>
  (agents.data.value?.items ?? []).map((a) => String(a.agentType ?? '')).filter((type) => type !== ''),
);
const agentOptions = computed(() => agentTypes.value.map((type) => ({ value: type, label: agentLabel(type) })));
/** Agent types named for this model; an unnamed agent never sees it. */
const picked = ref<string[]>([]);
/** agent type → priority within that agent's list; lower is tried first. */
const priorities = reactive<Record<string, string>>({});
const showPriorities = ref(false);
const prioPanelId = `ms-prio-${Math.random().toString(36).slice(2, 8)}`;
/** the catalogue answers after the sheet opens; seed once, then leave the operator's choice alone */
const seeded = ref(false);

/** An agent type the server invented after this build ships still reads as itself, not as `agents.X`. */
function agentLabel(type: string): string {
  const key = `agents.${type}`;
  return te(key) ? t(key) : type;
}

function seedAgents(): void {
  picked.value = [...agentTypes.value];
  for (const key of Object.keys(priorities)) delete priorities[key];
  for (const type of agentTypes.value) priorities[type] = DEFAULT_PRIORITY;
  seeded.value = agentTypes.value.length > 0;
}

const contextTokens = computed(() =>
  form.maxContextTokens.trim() === '' ? Number(DEFAULT_CONTEXT) : Number(form.maxContextTokens),
);

const problems = computed<string[]>(() => {
  const out = new Set<string>();
  if (!form.provider) out.add(t('governance.providerRequired'));
  if (!form.model.trim()) out.add(t('governance.modelRequired'));
  for (const value of [form.priceIn, form.priceOut, ...picked.value.map((type) => priorities[type] ?? '')]) {
    if (value !== '' && Number.isNaN(Number(value))) out.add(t('governance.numbersOnly'));
  }
  if (!Number.isFinite(contextTokens.value) || contextTokens.value <= 0) out.add(t('governance.contextMustBePositive'));
  return [...out];
});

/** A named agent with no readable number keeps the fallback position the sheet started it at. */
function applicableAgents(): Record<string, number> {
  return Object.fromEntries(
    picked.value.map((type) => {
      const entered = (priorities[type] ?? '').trim();
      const value = entered === '' ? Number(DEFAULT_PRIORITY) : Number(entered);
      return [type, Number.isFinite(value) ? value : Number(DEFAULT_PRIORITY)];
    }),
  );
}

/** Two agent→priority maps, compared as the sets of pairs they are. */
function sameMap(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => Number(a[key]) === Number(b[key]));
}

/** Load the stored row: what it says is the answer, and nothing is waiting to be seeded. */
function prefill(row: T.ModelView): void {
  const caps = (row.capabilities ?? {}) as Record<string, unknown>;
  form.provider = String(row.provider ?? '');
  form.model = String(row.model ?? '');
  form.structuredOutput = caps.structuredOutput !== false;
  form.vision = caps.vision === true;
  form.maxContextTokens = String(Number(caps.maxContextTokens) || DEFAULT_CONTEXT);
  form.priceIn = String(row.unitPriceInputPer1k ?? 0);
  form.priceOut = String(row.unitPriceOutputPer1k ?? 0);
  form.enabled = row.enabled !== false;
  const named = Object.entries((row.applicableAgents ?? {}) as Record<string, unknown>);
  picked.value = named.map(([type]) => type);
  for (const key of Object.keys(priorities)) delete priorities[key];
  for (const [type, value] of named) {
    const priority = Number(value);
    priorities[type] = String(Number.isFinite(priority) ? priority : Number(DEFAULT_PRIORITY));
  }
  seeded.value = true;
}

watch(show, (open) => {
  if (!open) return;
  showPriorities.value = false;
  const row = stored.value;
  if (row) {
    prefill(row);
    return;
  }
  form.provider = String(providerList.value[0]?.name ?? '');
  form.model = '';
  form.structuredOutput = true;
  form.vision = false;
  form.maxContextTokens = DEFAULT_CONTEXT;
  form.priceIn = '0';
  form.priceOut = '0';
  form.enabled = true;
  seedAgents();
});

watch(agentTypes, () => {
  if (show.value && !seeded.value) seedAgents();
});

// the guided step can open this sheet while the provider list is still in flight, and a sheet that
// pre-selects nothing asks the operator to choose a row they cannot see yet
watch(providerList, (rows) => {
  if (show.value && form.provider === '') form.provider = String(rows[0]?.name ?? '');
});

/**
 * Only what the operator moved; an unchanged field is left off the wire entirely.
 *
 * `endpoint` and `apiKeySecretRef` are never sent, which is the point of this body: S3 writes NULL
 * over a column a registration omits, so an edit that did not re-type the model's own address
 * would have taken the model back onto the provider's -- and S1's read path then reports the
 * provider's address as though the row still had no override of its own.
 */
function patchBody(): T.PatchModelRequest {
  const row = stored.value;
  if (!row) return {};
  const caps = (row.capabilities ?? {}) as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  const capabilityPatch: Record<string, unknown> = {};
  if (form.structuredOutput !== Boolean(caps.structuredOutput)) capabilityPatch.structuredOutput = form.structuredOutput;
  if (form.vision !== Boolean(caps.vision)) capabilityPatch.vision = form.vision;
  if (contextTokens.value !== Number(caps.maxContextTokens)) capabilityPatch.maxContextTokens = contextTokens.value;
  if (Object.keys(capabilityPatch).length) out.capabilities = capabilityPatch;
  if ((Number(form.priceIn) || 0) !== Number(row.unitPriceInputPer1k ?? 0)) out.unitPriceInputPer1k = Number(form.priceIn) || 0;
  if ((Number(form.priceOut) || 0) !== Number(row.unitPriceOutputPer1k ?? 0)) out.unitPriceOutputPer1k = Number(form.priceOut) || 0;
  const agentsNow = applicableAgents();
  if (!sameMap((row.applicableAgents ?? {}) as Record<string, unknown>, agentsNow)) out.applicableAgents = agentsNow;
  if (form.enabled !== (row.enabled !== false)) out.enabled = form.enabled;
  return out as T.PatchModelRequest;
}

const unchanged = computed(() => !!stored.value && Object.keys(patchBody()).length === 0);

async function submit(): Promise<void> {
  if (problems.value.length || unchanged.value) return;
  const row = stored.value;
  if (row) {
    await updateModel.mutateAsync({ ref: String(row.ref ?? row.id), body: patchBody() });
  } else {
    await registerModel.mutateAsync({
      provider: form.provider,
      model: form.model.trim(),
      enabled: form.enabled,
      capabilities: {
        structuredOutput: form.structuredOutput,
        vision: form.vision,
        // always sent: leaving the key off is what the server reads as a zero window
        maxContextTokens: contextTokens.value,
      },
      unitPriceInputPer1k: Number(form.priceIn) || 0,
      unitPriceOutputPer1k: Number(form.priceOut) || 0,
      applicableAgents: applicableAgents(),
    });
  }
  show.value = false;
  pushSuccess(row ? t('governance.modelUpdated') : t('governance.modelSaved'));
  emit('saved');
}
</script>

<template>
  <FuiBottomSheet v-model="show" :title="stored ? t('governance.editModel') : t('governance.registerModel')">
    <p v-if="!providerList.length" class="ms__warn">{{ t('governance.noProvidersYet') }}</p>
    <p v-for="problem in problems" :key="problem" class="ms__warn">{{ problem }}</p>
    <div class="ms__group">
      <span class="ms__legend">{{ t('governance.providers') }}</span>
      <!-- a model's provider is half of its reference; moving one means registering the other -->
      <FuiSegmented v-model="form.provider" :options="providerOptions" :disabled="!!stored" :label="t('governance.providers')" />
    </div>
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpProvider')" />
    <FuiField v-model="form.model" :label="t('governance.modelId')" :placeholder="t('governance.modelIdPlaceholder')" :readonly="!!stored" required />
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpModelId')" />
    <!-- a shortcut into the same text box, never a separate value -->
    <div v-if="!stored && modelSuggestions.length" class="ms__group">
      <span class="ms__legend">{{ t('governance.modelSuggestions') }}</span>
      <div class="ms__suggestions">
        <FuiButton
          v-for="name in modelSuggestions"
          :key="name"
          size="mini"
          :label="name"
          @click="form.model = name"
        />
      </div>
    </div>
    <FuiCell :title="t('governance.structuredOutput')">
      <FuiSwitch v-model="form.structuredOutput" size="20" :label="t('governance.structuredOutput')" />
    </FuiCell>
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpStructured')" />
    <FuiCell :title="t('governance.vision')">
      <FuiSwitch v-model="form.vision" size="20" :label="t('governance.vision')" />
    </FuiCell>
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpVision')" />
    <FuiField v-model="form.maxContextTokens" :label="t('governance.maxContext')" type="number" />
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpMaxContext')" />
    <FuiField v-model="form.priceIn" :label="t('governance.priceIn')" type="number" />
    <FuiField v-model="form.priceOut" :label="t('governance.priceOut')" type="number" />
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpPrices')" />
    <FuiCell :title="t('governance.enabledLabel')">
      <FuiSwitch v-model="form.enabled" size="20" :label="t('governance.enabledLabel')" />
    </FuiCell>
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpEnabled')" />
    <div class="ms__group">
      <span class="ms__legend">{{ t('governance.applicableAgents') }}</span>
      <FuiCheckChips v-model="picked" :options="agentOptions" :label="t('governance.applicableAgents')" />
      <FuiButton
        size="mini"
        :label="t('governance.selectAllAgents')"
        :disabled="picked.length === agentTypes.length"
        @click="seedAgents"
      />
      <FuiButton
        size="mini"
        :label="t('governance.editPriorities')"
        :aria-expanded="showPriorities"
        :aria-controls="showPriorities ? prioPanelId : undefined"
        @click="showPriorities = !showPriorities"
      />
    </div>
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpAgents')" />
    <div v-if="showPriorities" :id="prioPanelId" role="group" :aria-label="t('governance.priority')" class="ms__group">
      <FuiField
        v-for="type in picked"
        :key="type"
        v-model="priorities[type]"
        :label="agentLabel(type)"
        type="number"
      />
    </div>
    <!-- The ordering sentence stays outside the collapsible panel on purpose: every agent is
         submitted with the fallback number whether or not the panel is ever opened, so an operator
         who never expands it is still setting priorities — and a main model left at 100 behind a
         stub at 1 never gets tried (S3's select() walks applicableAgents ascending). -->
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpPriority')" />
    <p v-if="!picked.length" class="ms__warn">{{ t('governance.noAgentsSelected') }}</p>
    <template #footer>
      <FuiActionButton
        variant="primary"
        platform
        block
        :action="stored ? ACTION.governanceModelEnable : ACTION.governanceModelRegister"
        :loading="stored ? updateModel.isPending.value : registerModel.isPending.value"
        :disabled="!!problems.length || unchanged"
        :label="t('common.save')"
        @click="submit"
      />
    </template>
  </FuiBottomSheet>
</template>

<style scoped>
.ms__warn { color: var(--fui-warning); font-size: var(--fui-fs-sm); margin: var(--fui-sp-1) 0; }
.ms__group { display: grid; gap: var(--fui-sp-1); }
.ms__legend { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); }
.ms__suggestions { display: flex; flex-wrap: wrap; gap: var(--fui-sp-1); }
</style>
