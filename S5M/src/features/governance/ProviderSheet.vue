<script setup lang="ts">
/**
 * Register or edit one model provider (S5IF-GOV02/03, UI-SET-006).
 *
 * Extracted from the governance page so the guided setup can open the *same* sheet from its own
 * step: two copies of this form would be two definitions of what a valid registration is, and the
 * first server rule to move would leave one of them wrong.
 *
 * The vendor picker is S1's catalogue (S5IF-GOV07) — a starting point, never a cage. `custom`
 * leaves both fields free text, which is what a private gateway or an internal vLLM needs.
 */
import { computed, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiBottomSheet, FuiButton, FuiField, FuiSwitch, FuiSegmented } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import FuiHelp from '@/fui/FuiHelp.vue';
import { ACTION } from '@/api/actions';
import CredentialField from '@/features/secrets/CredentialField.vue';
import { useProviderCatalogue } from '@/api/queries';
import { useRegisterProvider, useUpdateProvider } from '@/api/mutations';
import { PROVIDER_CLASSES, isSecretReference, isHttpOrHttpsUrl } from '@/lib/enums';
import { providerClassLabel } from '@/lib/labels';
import { pushSuccess } from '@/lib/toast';
import type * as T from '@/api/types';

const props = defineProps<{ provider: T.ProviderView | null }>();
const show = defineModel<boolean>({ required: true });
const emit = defineEmits<{ (e: 'saved'): void }>();

const { t } = useI18n();
const catalogue = useProviderCatalogue();
const presets = computed(() => catalogue.data.value?.items ?? []);
const registerProvider = useRegisterProvider();
const editProvider = useUpdateProvider();

/** One line in the form, an array on the wire; both half- and full-width separators are accepted. */
function parseModels(text: string): string[] {
  return text
    .split(/[,;，；\n]/)
    .map((name) => name.trim())
    .filter(Boolean);
}
function modelsText(models?: readonly string[] | null): string {
  return (models ?? []).join(', ');
}
function sameModels(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((name, index) => name === b[index]);
}

const form = reactive({
  name: '',
  providerClass: 'PRIVATE_NETWORK',
  endpoint: '',
  apiKeySecretRef: '',
  suggestedModels: '',
  adapter: 'openai_compat',
  enabled: true,
});
/** `custom` keeps the address field editable; a preset only fills it in. */
const vendorKey = ref('custom');
const selectedPreset = computed(() => presets.value.find((row) => String(row.key) === vendorKey.value));
const suggestedKeyRef = computed(() => String(selectedPreset.value?.keyRefSuggestion ?? ''));
/** A stored key this build no longer lists still has to read as itself, not as the first option. */
const unknownVendor = computed(
  () => vendorKey.value !== 'custom' && !presets.value.some((row) => String(row.key) === vendorKey.value),
);
const pending = computed(() => registerProvider.isPending.value || editProvider.isPending.value);

// one map for the whole app, in `lib/labels.ts` — see `providerClassLabel` for why a second copy
// and an i18next-style `{ defaultValue }` option were both wrong here
const classLabel = providerClassLabel;
const classOptions = computed(() => PROVIDER_CLASSES.map((c) => ({ value: c, label: classLabel(c) })));

// S3 refuses a call only when the address is empty and never inspects its scheme, so an http
// endpoint on a private network is legitimate — the data policy governs egress, not this.
const problems = computed<string[]>(() => {
  const out = new Set<string>();
  if (!form.name.trim() && !props.provider) out.add(t('governance.providerNameRequired'));
  if (!isHttpOrHttpsUrl(form.endpoint.trim())) out.add(t('governance.endpointRequired'));
  // empty means "no key needed", which is S1's own reading for a local server; a non-empty value
  // still has to be a reference and never the key itself
  if (form.apiKeySecretRef.trim() && !isSecretReference(form.apiKeySecretRef)) out.add(t('environments.referenceRequired'));
  return [...out];
});

/** Only what the operator moved; an unchanged field is left off the wire entirely. */
function patchBody(): T.PatchProviderRequest {
  const stored = props.provider;
  const out: Record<string, unknown> = {};
  if (!stored) return out;
  if (form.providerClass !== String(stored.providerClass ?? '')) out.providerClass = form.providerClass;
  if (form.endpoint !== (stored.endpoint ?? '')) out.endpoint = form.endpoint;
  // an emptied box is not a value PATCH can carry (`min_length=1`), and there is no removal to ask
  // for: absence is S1's spelling of "leave the stored reference alone", so the cleared text stays
  // on the screen rather than going out as a 422 the operator cannot act on
  if (form.apiKeySecretRef.trim() !== '' && form.apiKeySecretRef !== (stored.apiKeySecretRef ?? ''))
    out.apiKeySecretRef = form.apiKeySecretRef;
  // `adapter` is deliberately not patched. The form holds a constant (this build speaks one wire
  // protocol and `provider_for()` ignores the column), so the only way this line could fire is a
  // row stored under some other value — and editing that row's display name would then silently
  // rewrite a column the operator never looked at. S5 does the same on purpose.
  if (form.enabled !== (stored.enabled ?? true)) out.enabled = form.enabled;
  if (vendorKey.value !== String(stored.vendor ?? 'custom')) out.vendor = vendorKey.value;
  const models = parseModels(form.suggestedModels);
  if (!sameModels(models, stored.suggestedModels ?? [])) out.suggestedModels = models;
  return out as T.PatchProviderRequest;
}

const unchanged = computed(() => !!props.provider && Object.keys(patchBody()).length === 0);

watch(show, (open) => {
  if (!open) return;
  const stored = props.provider;
  form.name = stored?.name ?? '';
  form.providerClass = String(stored?.providerClass ?? 'PRIVATE_NETWORK');
  form.endpoint = stored?.endpoint ?? '';
  form.apiKeySecretRef = stored?.apiKeySecretRef ?? '';
  form.suggestedModels = modelsText(stored?.suggestedModels);
  form.adapter = stored?.adapter ?? 'openai_compat';
  form.enabled = stored?.enabled ?? true;
  // the stored key is the honest answer; matching the address again would relabel a legacy row
  // as a preset the operator never chose and then write that label back on the next save
  vendorKey.value = String(stored?.vendor ?? 'custom');
});

/**
 * A vendor change fills the address and model names that vendor's documentation states — but
 * only where the operator has not typed something of their own. Comparing against the previous
 * preset's values is what carries a hand-edited proxy address across the change.
 */
function chooseVendor(key: string): void {
  const previous = presets.value.find((row) => String(row.key) === vendorKey.value);
  const next = presets.value.find((row) => String(row.key) === key);
  if (form.endpoint.trim() === '' || form.endpoint === String(previous?.endpoint ?? '')) {
    form.endpoint = String(next?.endpoint ?? form.endpoint);
  }
  const currentModels = form.suggestedModels.trim();
  if (currentModels === '' || currentModels === modelsText(previous?.models)) {
    form.suggestedModels = modelsText(next?.models);
  }
  vendorKey.value = key;
}

async function submit(): Promise<void> {
  if (problems.value.length) return;
  if (props.provider) {
    // PATCH leaves any field the body omits alone, while the register path overwrites every
    // column — so sending the touched set is both the smaller and the safer request
    await editProvider.mutateAsync({ name: props.provider.name ?? '', body: patchBody() });
  } else {
    await registerProvider.mutateAsync({
      name: form.name.trim(),
      providerClass: form.providerClass as T.RegisterProviderRequest['providerClass'],
      endpoint: form.endpoint,
      apiKeySecretRef: form.apiKeySecretRef,
      adapter: form.adapter,
      enabled: form.enabled,
      vendor: vendorKey.value,
      suggestedModels: parseModels(form.suggestedModels),
    });
  }
  show.value = false;
  pushSuccess(t('governance.providerSaved'));
  emit('saved');
}
</script>

<template>
  <FuiBottomSheet v-model="show" :title="provider ? t('governance.editProvider') : t('governance.registerProvider')">
    <p class="ps__hint">{{ t('governance.keyRefHint') }}</p>
    <FuiField v-model="form.name" :label="t('governance.providerName')" :readonly="!!provider" required />
    <select
      class="ps__pick"
      :aria-label="t('governance.vendor')"
      :value="vendorKey"
      @change="chooseVendor(($event.target as HTMLSelectElement).value)"
    >
      <option v-for="preset in presets" :key="String(preset.key)" :value="String(preset.key)">
        {{ preset.displayName }}{{ preset.local ? ' · ' + t('governance.vendorLocal') : '' }}
      </option>
      <option v-if="unknownVendor" :value="vendorKey">{{ vendorKey }}</option>
      <option value="custom">{{ t('governance.vendorCustom') }}</option>
    </select>
    <div v-if="selectedPreset" class="ps__details">
      <p v-if="selectedPreset.note" class="ps__hint">{{ selectedPreset.note }}</p>
      <p v-if="selectedPreset.local" class="ps__hint">{{ t('governance.vendorLocalHelp') }}</p>
      <a
        v-if="selectedPreset.docsUrl"
        class="ps__link"
        :href="String(selectedPreset.docsUrl)"
        target="_blank"
        rel="noreferrer"
      >
        {{ t('governance.vendorDocs') }}
      </a>
    </div>
    <FuiSegmented v-model="form.providerClass" :options="classOptions" :label="t('governance.providerClass')" />
    <FuiHelp :label="t('common.whyThis')" :text="t('governance.helpProviderClass')" />
    <FuiField v-model="form.endpoint" :label="t('governance.endpoint')" type="url" required />
    <p class="ps__hint">{{ t('governance.endpointHelp') }}</p>
    <CredentialField
      v-model="form.apiKeySecretRef"
      :label="t('governance.apiKeyRef')"
      :help="t('governance.keyOptionalHelp')"
      :suggested-name="'llm-' + (form.name || 'provider').toLowerCase().replace(/[^a-z0-9._-]+/g, '-')"
    />
    <!-- the catalogue's suggestion is offered, never stamped in: an env:// reference the
         deployment never set registers cleanly and fails the first call -->
    <div v-if="suggestedKeyRef && !form.apiKeySecretRef.trim()" class="ps__actions">
      <FuiButton
        size="mini"
        :label="t('governance.useSuggestedKey', { ref: suggestedKeyRef })"
        @click="form.apiKeySecretRef = suggestedKeyRef"
      />
    </div>
    <FuiField
      v-model="form.suggestedModels"
      :label="t('governance.suggestedModels')"
      :placeholder="t('governance.suggestedModelsPlaceholder')"
      :help="t('governance.suggestedModelsHelp')"
    />
    <!-- one wire protocol exists in this build and `adapter` does not select another, so it is
         stated rather than offered (S3's provider_for ignores the column) -->
    <p class="ps__hint">{{ t('governance.adapterFixed', { adapter: form.adapter }) }}</p>
    <FuiSwitch v-model="form.enabled" size="20" :label="t('governance.enabledLabel')" />
    <p v-for="problem in problems" :key="problem" class="ps__err">{{ problem }}</p>
    <template #footer>
      <!-- UM-BR-01: the local `problems` check says the form is well-formed; only S1 says whether
           this operator may write it at all. -->
      <FuiActionButton
        platform
        :action="ACTION.governanceProviderRegister"
        variant="primary"
        block
        :loading="pending"
        :disabled="problems.length > 0 || unchanged"
        :label="t('common.save')"
        @click="submit"
      />
    </template>
  </FuiBottomSheet>
</template>

<style scoped>
.ps__hint { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin: var(--fui-sp-1) 0; }
.ps__err { color: var(--fui-danger); font-size: var(--fui-fs-sm); margin: var(--fui-sp-1) 0; }
.ps__details { display: grid; gap: var(--fui-sp-1); }
.ps__link { color: var(--fui-primary); font-size: var(--fui-fs-xs); min-height: var(--fui-tap-min); display: inline-flex; align-items: center; }
.ps__actions { display: flex; flex-wrap: wrap; gap: var(--fui-sp-2); }
.ps__pick { min-height: var(--fui-tap-min); width: 100%; font-size: var(--fui-fs-sm); color: var(--fui-text); background: var(--fui-surface); border: 1px solid var(--fui-border); border-radius: var(--fui-radius); padding: 0 var(--fui-sp-2); }
</style>
