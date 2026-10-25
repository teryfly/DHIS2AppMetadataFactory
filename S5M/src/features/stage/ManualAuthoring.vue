<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiSchemaForm, FuiBottomSheet, FuiCard, FuiButton, FuiActionButton, FuiJsonEditor, FuiStatusTag, FuiSkeleton, FuiDialog, FuiNotice } from '@/fui';
import { useSectionSchema, useBlueprintDraft } from '@/api/queries';
import { useSubmitPatch } from '@/api/mutations';
import { ACTION } from '@/api/actions';
import { LS, readJSON, writeJSON, removeKey } from '@/lib/storage';
import { pushSuccess } from '@/lib/toast';
import { deref } from '@/lib/json/resolveSchema';
import type { JsonSchema } from '@/fui/schemaTypes';

const props = defineProps<{ modelValue: boolean; stageId: string; projectId: string; stageType: string; workflowId: string }>();
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();

const { t } = useI18n();
const router = useRouter();

// Stages that support manual authoring map to an S2 section (UM-MAN-002).
const SECTION_BY_STAGE_TYPE: Record<string, string> = { REQUIREMENTS: 'requirements', DOMAIN: 'domain', MAPPING: 'mapping', PAGE_DESIGN: 'pages' };
const section = computed(() => SECTION_BY_STAGE_TYPE[props.stageType] ?? '');

const schemaQ = useSectionSchema(() => props.projectId, section);
const draftQ = useBlueprintDraft(() => props.projectId);
const patch = useSubmitPatch(() => props.workflowId);

const model = ref<Record<string, unknown>>({});
const rawSchema = computed<JsonSchema | undefined>(() => schemaQ.data.value as unknown as JsonSchema | undefined);
// A section document may root at a $ref; resolving it is what lets the form and the editor agree
// on which fields exist (S2 sections route their shared types through #/definitions/*).
const schema = computed<JsonSchema | undefined>(() => (rawSchema.value ? (deref(rawSchema.value, rawSchema.value).schema ?? undefined) : undefined));
const hasFields = computed(() => Object.keys(schema.value?.properties ?? {}).length > 0);

const draftKey = computed(() => LS.draftPrefix + props.stageId);
const restorePrompt = ref(false);
const jsonMode = ref(false);
const editor = ref<InstanceType<typeof FuiJsonEditor> | null>(null);

function enterJson(): void {
  jsonMode.value = true;
}
/**
 * Leaving the JSON view used to clear the error and keep the stale model — the one place this
 * screen could silently drop work. The editor owns that guard now.
 */
function enterForm(): void {
  if (editor.value?.requestLeave?.() === false) return;
  jsonMode.value = false;
}

const userEdited = ref(false);
const dirty = ref(false);
/** False until a seed actually arrived — see `submitPatch`. */
const loaded = ref(false);
const isRequirements = computed(() => section.value === 'requirements');

function seedFromDraft(): void {
  const doc = (draftQ.data.value as Record<string, unknown> | undefined)?.document as Record<string, unknown> | undefined;
  const base = doc && section.value ? doc[section.value] : undefined;
  loaded.value = !!base && typeof base === 'object';
  if (base && typeof base === 'object') {
    model.value = structuredClone(base as Record<string, unknown>);
    dirty.value = false;
  }
}

function updateModel(v: Record<string, unknown>): void {
  model.value = v;
  userEdited.value = true;
  dirty.value = true;
}

onMounted(() => {
  if (readJSON<Record<string, unknown> | null>(draftKey.value, null)) restorePrompt.value = true;
  else seedFromDraft();
});

// Server draft may resolve after mount; seed it once unless the user already edited or has a local draft.
watch(() => draftQ.data.value, () => {
  if (!userEdited.value && !readJSON<Record<string, unknown> | null>(draftKey.value, null)) seedFromDraft();
});

// 5s autosave of the local draft (UM-MAN-002)
let timer: ReturnType<typeof setInterval> | null = null;
onMounted(() => {
  timer = setInterval(() => {
    if (dirty.value) {
      writeJSON(draftKey.value, model.value);
      dirty.value = false;
    }
  }, 5000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

function resumeDraft(): void {
  const saved = readJSON<Record<string, unknown> | null>(draftKey.value, null);
  if (saved) {
    model.value = saved;
    loaded.value = true;
    userEdited.value = true;
    dirty.value = false;
  }
  restorePrompt.value = false;
}
function discardDraft(): void {
  removeKey(draftKey.value);
  userEdited.value = false;
  seedFromDraft();
  restorePrompt.value = false;
}

/**
 * What S2 actually reads is RFC 6902 operations (`adapters/registry.py` refuses a body without a
 * non-empty `operations` list with 422), so a whole-section document sent as `{ <section>: model }`
 * was never a patch this server could accept.
 *
 * An empty model is not submitted: S1 pins `blueprint/draft`'s `document` to null, so a sheet that
 * never received a seed would otherwise overwrite the section with `{}`.
 */
function submitPatch(): void {
  if (!loaded.value || !section.value) return;
  patch.mutate(
    { stageId: props.stageId, projectId: props.projectId, body: { patch: { operations: [{ op: 'add', path: `/${section.value}`, value: model.value }], rationale: t('manual.title') } } },
    {
      onSuccess: () => {
        removeKey(draftKey.value);
        pushSuccess(t('manual.submitted'));
      },
    },
  );
}

const patchResult = computed<Record<string, unknown> | undefined>(() => patch.data.value as unknown as Record<string, unknown> | undefined);
const close = () => emit('update:modelValue', false);
/** Requirements have their own screen now: one entry per row, with the list and the gates around it. */
function goEntries(): void {
  router.push(`/projects/${props.projectId}/requirements`);
  close();
}
</script>

<template>
  <FuiBottomSheet :model-value="modelValue" :title="t('manual.title')" @update:model-value="(v: boolean) => emit('update:modelValue', v)">
    <p v-if="!section" class="ma__muted">{{ t('common.notFound') }}</p>

    <FuiCard v-else-if="isRequirements" flat>
      <p class="ma__muted">{{ t('reqItems.fromStageHint') }}</p>
      <FuiButton block variant="primary" :label="t('reqItems.title')" @click="goEntries" />
    </FuiCard>

    <template v-else>
      <FuiNotice v-if="!loaded" tone="info" :text="t('manual.notSeeded')" />
      <div class="ma__tabs">
        <FuiButton size="small" :variant="jsonMode ? 'ghost' : 'primary'" :label="t('manual.form')" @click="enterForm" />
        <FuiButton size="small" :variant="jsonMode ? 'primary' : 'ghost'" :label="t('manual.json')" @click="enterJson" />
      </div>

      <FuiCard v-if="!jsonMode" flat>
        <FuiSkeleton :loading="schemaQ.isLoading.value" :rows="4">
          <FuiSchemaForm v-if="hasFields" :schema="schema" :model-value="model" @update:model-value="updateModel" />
          <p v-else class="ma__muted">{{ t('common.notFound') }} · {{ t('manual.desktopJsonSoon') }}</p>
        </FuiSkeleton>
      </FuiCard>
      <FuiCard v-else flat :title="t('manual.json')">
        <FuiJsonEditor
          ref="editor"
          :model-value="model"
          :schema="schema"
          @update:model-value="updateModel"
        />
      </FuiCard>

      <div v-if="patchResult" class="ma__result">
        <FuiStatusTag :tone="patchResult.rejected ? 'danger' : 'success'" :label="patchResult.rejected ? t('manual.patchRejected') : t('manual.submitted')" />
        <pre class="ma__pre" role="region" tabindex="0" :aria-label="t('common.rawContent')">{{ JSON.stringify(patchResult, null, 2) }}</pre>
      </div>
    </template>

    <template #footer>
      <div class="ma__foot">
        <FuiButton block :label="t('common.cancel')" @click="close" />
        <FuiActionButton
          v-if="section && !isRequirements"
          :workflow-id="workflowId"
          :action="ACTION.patchSubmit"
          :target-id="stageId"
          variant="primary"
          block
          :label="t('common.submit')"
          :disabled="!loaded"
          :loading="patch.isPending.value"
          @click="submitPatch"
        />
      </div>
    </template>

    <FuiDialog v-model:show="restorePrompt" :title="t('manual.continueDraft')" show-cancel-button :confirm-button-text="t('common.confirm')" :cancel-button-text="t('manual.discardDraft')" @confirm="resumeDraft" @cancel="discardDraft" />
  </FuiBottomSheet>
</template>

<style scoped>
.ma__tabs { display: flex; gap: 8px; margin-bottom: var(--fui-sp-2); }
.ma__muted { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); text-align: center; padding: var(--fui-sp-4); }
.ma__result { margin-top: var(--fui-sp-3); }
.ma__pre { white-space: pre-wrap; font-size: var(--fui-fs-xs); background: var(--fui-surface-2); padding: 8px; border-radius: 8px; max-height: 30vh; overflow: auto; }
.ma__foot { display: flex; gap: 8px; }
</style>
