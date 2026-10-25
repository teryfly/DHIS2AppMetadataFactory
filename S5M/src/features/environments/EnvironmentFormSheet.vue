<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiBottomSheet, FuiButton, FuiField, FuiSwitch, FuiNotice, FuiSegmented, FuiCheckChips } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import CredentialField from '@/features/secrets/CredentialField.vue';
import { useCreateEnvironment, useUpdateEnvironment } from '@/api/mutations';
import { useEnvironmentProtection } from '@/api/queries';
import { pushSuccess } from '@/lib/toast';
import { AppError } from '@/api/errors';
import {
  AUTH_MODES, CREDENTIAL_SCOPES, DATA_POLICIES, ENVIRONMENT_CLASSES,
  isHttpOrHttpsUrl, isHttpsUrl, isSecretReference, REQUIRED_CREDENTIAL_SCOPES,
} from '@/lib/enums';
import type * as T from '@/api/types';

// One sheet for create and edit. The distinction is real, not cosmetic: S1's POST takes the whole
// record and its PATCH takes only what changed, so an edit that sent the key back would be a
// field the contract does not have on that path.
const props = defineProps<{ modelValue: boolean; environment: T.EnvironmentView | null }>();
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>();

const { t } = useI18n();

/** A credential name is a slug, and the environment key is the only sensible thing to build it from. */
function slug(value: string): string {
  return (String(value || 'env').toLowerCase().match(/[a-z0-9._-]+/g) ?? ['env']).join('-').slice(0, 40) || 'env';
}

// typed against the enum lists, so a contract value added or renamed fails the typecheck here
const CLASS_LABEL: Record<(typeof ENVIRONMENT_CLASSES)[number], string> = {
  DEV: 'environments.classDEV', TEST: 'environments.classTEST', UAT: 'environments.classUAT', PRODUCTION: 'environments.classPROD',
};
const AUTH_LABEL: Record<(typeof AUTH_MODES)[number], string> = {
  PAT: 'environments.authPAT', BASIC: 'environments.authBASIC',
};
const POLICY_LABEL: Record<(typeof DATA_POLICIES)[number], string> = {
  LOCAL_ONLY: 'environments.policyLocal', PRIVATE_NETWORK: 'environments.policyPrivate',
  APPROVED_EXTERNAL: 'environments.policyApproved', UNRESTRICTED: 'environments.policyUnrestricted',
};
const SCOPE_LABEL: Record<(typeof CREDENTIAL_SCOPES)[number], string> = {
  discovery: 'environments.scopeDiscovery', import: 'environments.scopeImport', tracker: 'environments.scopeTracker',
  datastore: 'environments.scopeDatastore', apps: 'environments.scopeApps', maintenance: 'environments.scopeMaintenance',
};

type Credential = { scope: string; secretRef: string };
type WindowDraft = { days: number[]; start: string; end: string };

/** S4's `CommitWindow`: ISO weekdays, 1 = Monday, and a clock range it reads as 'HH:MM'. */
// S1's weekday numbering (1 = Monday … 7 = Sunday), carried through the chips as text and turned
// back into numbers on the way out.
const DAY_VALUES = ['1', '2', '3', '4', '5', '6', '7'];
const CLOCK = /^([01]?\d|2[0-3]):[0-5]\d$/;

const form = reactive({
  environmentKey: '',
  name: '',
  baseUrl: '',
  environmentClass: 'TEST',
  authMode: 'PAT',
  versionConstraint: '',
  llmDataPolicy: 'PRIVATE_NETWORK',
  timeoutSeconds: '60',
  tlsRequired: true,
  notes: '',
  credentials: [{ scope: 'discovery', secretRef: '' }, { scope: 'import', secretRef: '' }] as Credential[],
  // `null` means the operator has not touched the protection block, so the sheet edits what S1
  // reported rather than an empty list that would replace the stored windows on a save
  timezone: null as string | null,
  commitWindows: null as WindowDraft[] | null,
});

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return;
    const e = props.environment;
    form.environmentKey = e?.environmentKey ?? '';
    form.name = e?.name ?? '';
    form.baseUrl = e?.baseUrl ?? '';
    // A stored value outside the contract stays exactly as it is: coercing it here would make the
    // next save rewrite a column the operator never touched. `segments` shows it instead.
    form.environmentClass = e?.environmentClass || 'TEST';
    form.authMode = e?.authMode || 'PAT';
    form.versionConstraint = e?.versionConstraint ?? '';
    form.llmDataPolicy = e?.llmDataPolicy || 'PRIVATE_NETWORK';
    form.timeoutSeconds = String(e?.timeoutSeconds ?? 60);
    form.tlsRequired = e?.tlsRequired ?? true;
    form.notes = e?.notes ?? '';
    const stored = (e?.credentials ?? []).map((c) => ({ scope: String(c.scope ?? 'discovery'), secretRef: String(c.secretRef ?? '') }));
    form.credentials = stored.length ? stored : [{ scope: 'discovery', secretRef: '' }, { scope: 'import', secretRef: '' }];
    form.timezone = null;
    form.commitWindows = null;
  },
);

/**
 * The contract's values plus the stored one when it is not among them, so an out-of-date row is
 * shown rather than silently replaced — S1's PATCH would otherwise receive a rewrite nobody asked for.
 */
function segments(values: readonly string[], current: string): string[] {
  return current && !values.includes(current) ? [current, ...values] : [...values];
}


function addCredential(): void {
  const free = CREDENTIAL_SCOPES.find((s) => !form.credentials.some((c) => c.scope === s));
  form.credentials.push({ scope: free ?? 'discovery', secretRef: '' });
}
function removeCredential(index: number): void {
  form.credentials.splice(index, 1);
}

const creating = computed(() => !props.environment);
const create = useCreateEnvironment();
const update = useUpdateEnvironment();
const protection = useEnvironmentProtection(() => props.environment?.environmentKey ?? '');
const pending = computed(() => create.isPending.value || update.isPending.value);
const failure = computed(() => create.error.value ?? update.error.value);

/**
 * S4's rules read the class the environment was registered with. On an edit that is a fact about
 * the record, not a choice this sheet still has — and S1 answers a patch that names it with a 422
 * (DX-ENV-001), so offering the control would only produce an error nobody can act on.
 */
const classEffective = computed(() => props.environment?.environmentClass || form.environmentClass);
const production = computed(() => classEffective.value === 'PRODUCTION');

// One chip group per choice, labelled from the app's dictionaries rather than the wire tokens, and
// carrying its stored value even when the contract no longer lists it (see `segments`).
const classOptions = computed(() =>
  segments(ENVIRONMENT_CLASSES, classEffective.value).map((c) => ({
    value: c,
    label: c in CLASS_LABEL ? t(CLASS_LABEL[c as keyof typeof CLASS_LABEL]) : c,
  })),
);
const authOptions = computed(() =>
  segments(AUTH_MODES, form.authMode).map((a) => ({
    value: a,
    label: a in AUTH_LABEL ? t(AUTH_LABEL[a as keyof typeof AUTH_LABEL]) : a,
  })),
);
const policyOptions = computed(() =>
  segments(DATA_POLICIES, form.llmDataPolicy).map((p) => ({
    value: p,
    label: p in POLICY_LABEL ? t(POLICY_LABEL[p as keyof typeof POLICY_LABEL]) : p,
  })),
);
function scopeOptions(current: string): { value: string; label: string }[] {
  return segments(CREDENTIAL_SCOPES, current).map((s) => ({
    value: s,
    label: s in SCOPE_LABEL ? t(SCOPE_LABEL[s as keyof typeof SCOPE_LABEL]) : s,
  }));
}
const dayOptions = computed(() => DAY_VALUES.map((d) => ({ value: d, label: t(`environments.weekday${d}`) })));
const shownTimezone = computed(() => form.timezone ?? protection.data.value?.timezone ?? 'UTC');
const shownWindows = computed<WindowDraft[]>(() =>
  form.commitWindows ??
  (protection.data.value?.commitWindows ?? []).map((w) => ({
    days: [...(w.days ?? [])],
    start: w.start ?? '00:00',
    end: w.end ?? '23:59',
  })),
);
const protectionTouched = computed(() => form.timezone !== null || form.commitWindows !== null);
/** The stored schedule is only editable once S1 has answered it; replacing it blind would wipe it. */
const protectionLoaded = computed(() => creating.value || protection.isSuccess.value);

/**
 * S1's and S4's own rules, applied before the submit, so a click does not end in a 422 the
 * operator has to interpret: the four reference schemes, discovery + import for every class
 * (rules.py:85-96), DX-AC-02's separation for PRODUCTION, DX-ENV-001's version constraint,
 * DX-CLI-006's TLS/address agreement, DX-ENV-002's commit window. Deduplicated because one box
 * can break two rules.
 */
const problems = computed<string[]>(() => {
  const out = new Set<string>();
  const refs = form.credentials.filter((c) => isSecretReference(c.secretRef));
  if (!form.environmentKey.trim() || !form.baseUrl.trim()) out.add(t('environments.keyRequired'));
  if (/\s/.test(form.environmentKey)) out.add(t('environments.keyNoSpaces'));
  if (form.credentials.some((c) => c.secretRef.trim() !== '' && !isSecretReference(c.secretRef))) out.add(t('environments.referenceRequired'));
  const missing = REQUIRED_CREDENTIAL_SCOPES.filter((scope) => !refs.some((c) => c.scope === scope));
  if (missing.length) out.add(`${t('environments.credentialScopesRequired')}: ${missing.join(', ')}`);
  const duplicated = CREDENTIAL_SCOPES.filter((scope) => refs.filter((c) => c.scope === scope).length > 1);
  if (duplicated.length) out.add(`${t('environments.credentialScopeDuplicate')}: ${duplicated.join(', ')}`);
  if (production.value && !missing.length) {
    const discovery = refs.find((c) => c.scope === 'discovery')?.secretRef;
    const importRef = refs.find((c) => c.scope === 'import')?.secretRef;
    if (discovery === importRef) out.add(t('environments.productionRefs'));
  }
  if (!isHttpOrHttpsUrl(form.baseUrl)) out.add(t('environments.httpSchemeRequired'));
  else if (form.tlsRequired && !isHttpsUrl(form.baseUrl)) out.add(t('environments.httpsRequired'));
  if (
    production.value &&
    (creating.value || protectionTouched.value) &&
    (shownWindows.value.length === 0 ||
      shownWindows.value.some((w) => w.days.length === 0 || !CLOCK.test(w.start) || !CLOCK.test(w.end)))
  ) {
    out.add(t('environments.windowsRequired'));
  }
  if (!form.versionConstraint.trim()) out.add(t('environments.versionConstraintRequired'));
  // S4 re-validates the merged profile and refuses a blank display name; an emptied box on an
  // edit is a mistake, not a value the contract can carry
  if (!creating.value && !form.name.trim()) out.add(t('environments.nameRequired'));
  if (!Number.isFinite(Number(form.timeoutSeconds)) || Number(form.timeoutSeconds) < 1) out.add(t('environments.timeoutRequired'));
  return [...out];
});

const canSubmit = computed(() => problems.value.length === 0 && changes.value.length > 0);

/** S1's own words for a rejection this form could not predict (an unknown class, a shared ref). */
const serverError = computed(() => (failure.value instanceof AppError ? failure.value.message : String(failure.value?.message ?? '')));

function credentialPayload() {
  return form.credentials
    .filter((c) => isSecretReference(c.secretRef))
    .map((c) => ({ scope: c.scope, secretRef: c.secretRef.trim() }));
}

function protectionPayload(): T.ProtectionSpec {
  return {
    timezone: shownTimezone.value,
    // the lock has its own endpoint, which is where a reason, an operator and a time are recorded
    // (DX-ENV-007); `false` here is the sheet saying it is not that operation
    emergencyLock: false,
    commitWindows: shownWindows.value.map((w) => ({
      days: [...w.days].sort((a, b) => a - b),
      start: w.start,
      end: w.end,
    })),
  };
}

function setDays(index: number, picked: readonly string[]): void {
  const days = picked.map(Number).sort((a, b) => a - b);
  form.commitWindows = shownWindows.value.map((w, i) => (i === index ? { ...w, days } : w));
}

function addWindow(): void {
  form.commitWindows = [...shownWindows.value, { days: [], start: '00:00', end: '23:59' }];
}

function patchWindow(index: number, change: Partial<WindowDraft>): void {
  form.commitWindows = shownWindows.value.map((w, i) => (i === index ? { ...w, ...change } : w));
}

function removeWindow(index: number): void {
  form.commitWindows = shownWindows.value.filter((_, i) => i !== index);
}

function createBody(): T.CreateEnvironmentRequest {
  return {
    environmentKey: form.environmentKey.trim(),
    name: form.name.trim() || undefined,
    baseUrl: form.baseUrl.trim(),
    environmentClass: form.environmentClass as T.CreateEnvironmentRequest['environmentClass'],
    versionConstraint: form.versionConstraint.trim(),
    authMode: form.authMode as T.CreateEnvironmentRequest['authMode'],
    tlsRequired: form.tlsRequired,
    llmDataPolicy: form.llmDataPolicy as T.CreateEnvironmentRequest['llmDataPolicy'],
    timeoutSeconds: Number(form.timeoutSeconds),
    notes: form.notes.trim() || undefined,
    credentials: credentialPayload() as T.CredentialSpec[],
    // only a production environment is asked for a schedule: giving one to a DEV environment
    // would register a rule its deployments never made
    ...(production.value ? { protection: protectionPayload() } : {}),
  };
}

/**
 * Only what the operator changed. S1's PATCH reads an absent field as "unchanged", so sending the
 * whole record back would rewrite columns nobody touched; `notes` is a field S4 lets an operator
 * empty, and emptying one has to travel as "" — omitting it would restore the stored value on the
 * next reload and read as a save that silently did nothing. `name` is not one of them: S4 refuses
 * an empty display name when it re-validates the merged profile.
 *
 * The class is absent by rule, not by omission (DX-ENV-001), and `protection` is sent only when
 * the sheet actually touched it — S4 replaces the whole window list, never merges into it.
 */
const patchBody = computed<T.UpdateEnvironmentRequest>(() => {
  const stored = props.environment;
  if (!stored) return {};
  const out: Record<string, unknown> = {};
  const put = (field: string, next: unknown, before: unknown): void => {
    if (next !== before) out[field] = next;
  };
  if (form.name.trim() !== (stored.name ?? '')) out.name = form.name.trim();
  put('notes', form.notes.trim(), stored.notes ?? '');
  put('baseUrl', form.baseUrl.trim(), stored.baseUrl ?? '');
  put('authMode', form.authMode, stored.authMode);
  put('llmDataPolicy', form.llmDataPolicy, stored.llmDataPolicy);
  put('versionConstraint', form.versionConstraint.trim(), stored.versionConstraint ?? '');
  put('tlsRequired', form.tlsRequired, stored.tlsRequired);
  put('timeoutSeconds', Number(form.timeoutSeconds), stored.timeoutSeconds);
  const asKeys = (rows: { scope: unknown; secretRef: unknown }[]): string =>
    rows.map((c) => `${String(c.scope)}\u0000${String(c.secretRef).trim()}`).join('\n');
  if (asKeys(credentialPayload()) !== asKeys(stored.credentials ?? [])) out.credentials = credentialPayload();
  if (protectionTouched.value) out.protection = protectionPayload();
  return out as T.UpdateEnvironmentRequest;
});

const changes = computed(() => Object.keys(creating.value ? createBody() : patchBody.value));

async function submit(): Promise<void> {
  if (!canSubmit.value) return;
  try {
    if (creating.value) {
      await create.mutateAsync(createBody());
    } else {
      await update.mutateAsync({ environmentKey: props.environment?.environmentKey ?? '', body: patchBody.value });
    }
  } catch {
    // surfaced by the global error toast (api/queryClient.ts); the sheet stays open to correct it
    return;
  }
  // A registered environment is not a usable one until a connection test has been recorded
  // against it; R3 refuses to validate without that evidence (DX-EXE-012).
  pushSuccess(t('environments.saved'));
  emit('saved');
  emit('update:modelValue', false);
}

function close(): void {
  emit('update:modelValue', false);
}
</script>

<template>
  <FuiBottomSheet :model-value="modelValue" :title="environment ? t('environments.edit') : t('environments.new')" @update:model-value="close">
    <FuiNotice tone="info" :text="t('environments.secretRefHint')" />
    <FuiNotice v-if="failure" tone="danger" :text="serverError" />

    <FuiField v-model="form.environmentKey" :label="t('environments.key')" :readonly="!creating" required />
    <FuiField v-model="form.name" :label="t('environments.envName')" />
    <FuiField v-model="form.baseUrl" :label="t('environments.baseUrl')" type="url" required />

    <div class="envf__group">
      <span class="envf__legend">{{ t('environments.class') }}</span>
      <!-- chosen at registration and never again: S1 answers a patch that names the class with
           a 422 (DX-ENV-001), so the control is closed rather than offering an error -->
      <FuiSegmented v-model="form.environmentClass" :options="classOptions" :label="t('environments.class')" :disabled="!creating" />
      <p v-if="!creating" class="envf__warn">{{ t('environments.classImmutable') }}</p>
      <p v-if="production" class="envf__warn">{{ t('environments.productionRefs') }}</p>
    </div>

    <div class="envf__group">
      <span class="envf__legend">{{ t('environments.authMode') }}</span>
      <FuiSegmented v-model="form.authMode" :options="authOptions" :label="t('environments.authMode')" />
    </div>

    <FuiField v-model="form.versionConstraint" :label="t('environments.versionConstraint')" placeholder="2.41.x" />

    <div class="envf__group">
      <span class="envf__legend">{{ t('environments.dataPolicy') }}</span>
      <FuiSegmented v-model="form.llmDataPolicy" :options="policyOptions" :label="t('environments.dataPolicy')" />
    </div>

    <FuiField v-model="form.timeoutSeconds" :label="t('environments.timeout')" type="number" />

    <div class="envf__group">
      <div class="envf__legendrow">
        <span class="envf__legend">{{ t('environments.credentials') }}</span>
        <FuiButton size="mini" :label="t('environments.addCredential')" @click="addCredential" />
      </div>
      <div v-for="(c, i) in form.credentials" :key="i" class="envf__cred">
        <FuiSegmented v-model="c.scope" small :options="scopeOptions(c.scope)" :label="t('environments.credentials')" />
        <CredentialField
          v-model="c.secretRef"
          :label="t('environments.secretRef')"
          :help="t('secrets.refHelp')"
          :shape="form.authMode === 'BASIC' ? 'basic' : 'single'"
          :suggested-name="'dhis2-' + slug(form.environmentKey) + '-' + c.scope"
        />
        <FuiButton size="mini" :label="t('environments.removeCredential')" @click="removeCredential(i)" />
      </div>
    </div>

    <span class="envf__legend">{{ t('environments.tlsRequired') }}</span>
    <FuiSwitch v-model="form.tlsRequired" size="20" :label="t('environments.tlsRequired')" />
    <FuiField v-model="form.notes" :label="t('environments.notes')" type="textarea" rows="2" />

    <!-- DX-ENV-002: a production environment commits only inside these windows, and S4 keeps the
         list whole rather than merging into it, so what is on screen here is what it will store -->
    <div v-if="production" class="envf__group">
      <span class="envf__legend">{{ t('environments.commitWindows') }}</span>
      <p class="envf__warn">{{ t('environments.commitWindowsHint') }}</p>
      <p v-if="!protectionLoaded" class="envf__warn">{{ t('environments.commitWindowsLoading') }}</p>
      <template v-else>
        <FuiField
          :model-value="shownTimezone"
          :label="t('environments.timezone')"
          :placeholder="'UTC'"
          @update:model-value="(v: string) => (form.timezone = v)"
        />
        <p class="envf__warn">{{ t('environments.timezoneHint') }}</p>
        <div v-for="(w, i) in shownWindows" :key="i" class="envf__cred">
          <FuiCheckChips
            small
            :model-value="w.days.map(String)"
            :options="dayOptions"
            :label="t('environments.commitWindows')"
            @update:model-value="(v: string[]) => setDays(i, v)"
          />
          <div class="envf__row">
            <FuiField
              :model-value="w.start"
              :label="t('environments.windowStart')"
              placeholder="01:00"
              @update:model-value="(v: string) => patchWindow(i, { start: v })"
            />
            <FuiField
              :model-value="w.end"
              :label="t('environments.windowEnd')"
              placeholder="05:00"
              @update:model-value="(v: string) => patchWindow(i, { end: v })"
            />
          </div>
          <FuiButton size="mini" :label="t('environments.removeWindow')" @click="removeWindow(i)" />
        </div>
        <FuiButton size="mini" :label="t('environments.addWindow')" @click="addWindow" />
      </template>
    </div>

    <p v-for="p in problems" :key="p" class="envf__err">{{ p }}</p>

    <template #footer>
      <!-- UM-BR-01: `canSubmit` is the UI's own opinion about the form; whether the operator may
           write at all is S1's. Both have to be true, and the server's reason is what shows. -->
      <FuiActionButton
        platform
        :action="creating ? ACTION.environmentCreate : ACTION.environmentEdit"
        variant="primary"
        block
        :loading="pending"
        :disabled="!canSubmit"
        :label="t('common.save')"
        @click="submit"
      />
    </template>
  </FuiBottomSheet>
</template>

<style scoped>
.envf__group { margin: 10px 0; }
.envf__legend { display: block; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin-bottom: 4px; }
.envf__legendrow { display: flex; align-items: center; justify-content: space-between; }
.envf__row { display: flex; gap: 8px; align-items: flex-end; }
.envf__cred { padding: 8px 0; border-bottom: 1px solid var(--fui-border); }
.envf__err, .envf__warn { color: var(--fui-danger, #d34d4d); font-size: var(--fui-fs-xs); margin: 4px 0; }
</style>
