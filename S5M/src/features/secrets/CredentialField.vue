<script setup lang="ts">
/**
 * S5IF-SEC01 / UM-ADM-002 as amended: the one control in this console that accepts a credential
 * *value*, and the reason it may is that nothing keeps it.
 *
 * The value exists between the keystroke and the request. After the answer comes back the fields are
 * cleared and what remains is the reference S1 returned — no read-back, no mask, no length
 * (S5IF-G09), nothing in localStorage, nothing in the URL. That is the bargain FR-SEC-007 strikes:
 * the browser sends a credential once and never receives it.
 *
 * `basic` composes `user:password` before storing, because that is the single value S4's Basic
 * authentication needs (`factory_dhis2/client/auth.py:37-53`); the two inputs are a convenience.
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useSecrets } from '@/api/queries';
import { useSaveSecret } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { AppError } from '@/api/errors';
import FuiField from '@/fui/FuiField.vue';
import FuiButton from '@/fui/FuiButton.vue';
import FuiActionButton from '@/fui/FuiActionButton.vue';

const props = withDefaults(
  defineProps<{
    /** The reference this field holds now (`env://…`, `db://…` or empty). */
    modelValue: string;
    label: string;
    /** Slug proposed for a new credential; the operator can change it. */
    suggestedName: string;
    shape?: 'single' | 'basic';
    error?: string;
    help?: string;
    disabled?: boolean;
  }>(),
  { shape: 'single', error: '', help: '', disabled: false },
);

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'blocked', reason: string): void;
}>();

const { t } = useI18n();
const stored = useSecrets();
const save = useSaveSecret();

const open = ref(false);
const panelId = `cred-panel-${Math.random().toString(36).slice(2, 8)}`;

/** Open the panel and move the caret into it, so the keyboard lands on the first field. */
async function toggle(): Promise<void> {
  open.value = !open.value;
  if (!open.value) return;
  await nextTick();
  panelRef.value?.querySelector<HTMLElement>('input, textarea, select')?.focus();
}
const panelRef = ref<HTMLElement | null>(null);
const name = ref(props.suggestedName);
const token = ref('');
const username = ref('');
const password = ref('');
const savedRef = ref('');
const failure = ref('');

watch(
  () => props.suggestedName,
  (next) => {
    name.value = next;
  },
);

const known = computed(() => stored.data.value?.items ?? []);
const material = computed(() => (props.shape === 'basic' ? `${username.value}:${password.value}` : token.value));
const canSave = computed(() => name.value.trim().length >= 2 && material.value.trim().length > 0 && !save.isPending.value);

/** Drop every trace of the value, whatever the request produced. */
function forget(): void {
  token.value = '';
  username.value = '';
  password.value = '';
}

async function submit(): Promise<void> {
  failure.value = '';
  try {
    const view = (await save.mutateAsync({ name: name.value.trim(), material: material.value })) as {
      ref?: string;
    };
    // The reference is S1's to issue, never this component's to guess. Synthesising `db://<name>`
    // here would fill the field with something that looks saved and may not be what the server
    // named — the whole point of the round trip is to get back the address the row actually has.
    const reference = String(view?.ref ?? '');
    if (!reference) {
      failure.value = t('secrets.failed');
      forget();
      return;
    }
    emit('update:modelValue', reference);
    savedRef.value = reference;
    forget();
    open.value = false;
  } catch (error) {
    forget();
    // 503 means this deployment never switched the store on; say what to do instead of "failed"
    failure.value =
      error instanceof AppError && error.errorCode === 'SECRET_UNAVAILABLE'
        ? t('secrets.unavailable')
        : t('secrets.failed');
  }
}
</script>

<template>
  <div class="cred">
    <FuiField
      :model-value="modelValue"
      :label="label"
      :placeholder="'env://…'"
      :error="error || undefined"
      :disabled="disabled"
      autocomplete="off"
      @update:model-value="emit('update:modelValue', String($event))"
    />
    <p v-if="help" class="cred__help">{{ help }}</p>

    <div class="cred__row">
      <!-- a disclosure has to say it is one, and put the caret where the work is -->
      <FuiButton
        size="mini"
        :label="open ? t('secrets.cancel') : t('secrets.enter')"
        :disabled="disabled"
        :aria-expanded="open"
        :aria-controls="panelId"
        @click="toggle"
      />
      <select
        v-if="known.length"
        class="cred__pick"
        :aria-label="t('secrets.useExisting')"
        :disabled="disabled"
        @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">{{ t('secrets.useExistingPlaceholder') }}</option>
        <option v-for="row in known" :key="String(row.name)" :value="String(row.ref)">{{ row.name }}</option>
      </select>
    </div>

    <p v-if="savedRef" class="cred__help">{{ t('secrets.saved', { ref: savedRef }) }}</p>
    <p v-if="failure" class="cred__error" role="alert">{{ failure }}</p>

    <div v-if="open" :id="panelId" ref="panelRef" :aria-label="t('secrets.enter')" class="cred__panel">
      <p class="cred__help">{{ t('secrets.whyBody') }}</p>
      <FuiField v-model="name" :label="t('secrets.name')" :help="t('secrets.nameHint')" :disabled="disabled" autocomplete="off" />
      <template v-if="shape === 'basic'">
        <FuiField v-model="username" :label="t('secrets.username')" :disabled="disabled" autocomplete="off" />
        <FuiField v-model="password" :label="t('secrets.password')" type="password" autocomplete="new-password" :disabled="disabled" />
        <p class="cred__help">{{ t('secrets.basicHint') }}</p>
      </template>
      <FuiField
        v-else
        v-model="token"
        :label="t('secrets.token')"
        type="password"
        autocomplete="new-password"
        :disabled="disabled"
      />
      <FuiActionButton
        platform
        :action="ACTION.secretManage"
        size="small"
        variant="primary"
        :loading="save.isPending.value"
        :disabled="!canSave"
        :label="t('secrets.save')"
        @click="submit"
        @blocked="emit('blocked', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.cred { display: grid; gap: var(--fui-sp-2); }
.cred__row { display: flex; align-items: center; gap: var(--fui-sp-2); flex-wrap: wrap; }
.cred__help { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); margin: 0; }
.cred__error { color: var(--fui-danger); font-size: var(--fui-fs-sm); margin: 0; }
.cred__panel { border: 1px solid var(--fui-border); border-radius: var(--fui-radius); padding: var(--fui-sp-3); display: grid; gap: var(--fui-sp-2); }
/* a native select: labelled, keyboard-operable, and read out by every platform (UM-NFR-03) */
.cred__pick { min-height: var(--fui-tap-min); font-size: var(--fui-fs-sm); color: var(--fui-text); background: var(--fui-surface); border: 1px solid var(--fui-border); border-radius: var(--fui-radius); padding: 0 var(--fui-sp-2); }
</style>
