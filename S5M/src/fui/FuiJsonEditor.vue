<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import FuiReadOnlyViewer from './FuiReadOnlyViewer.vue';
import FuiButton from './FuiButton.vue';
import FuiNotice from './FuiNotice.vue';
import FuiStatusTag from './FuiStatusTag.vue';
import type { Diagnostic } from '@/lib/json/sectionValidator';
import { isDesktop } from '@/lib/breakpoint';
import { t as translate } from '@/lib/labels';
import type { JsonSchema } from './schemaTypes';

/**
 * UM-CAP-001 JSON editor (desktop). The heavy CodeMirror core is loaded dynamically so the editor,
 * Ajv and jsonc-parser stay out of the first-screen bundle (UM-TECH-002); pages import this shell
 * and never see a third-party module (UM-TECH-001).
 *
 * On a phone the same component degrades to the read-only viewer with the desktop hand-off line,
 * which is what UM-P-06 asks for — the editing affordance is not shrunk, it is moved.
 */
const props = withDefaults(
  defineProps<{ modelValue: Record<string, unknown>; schema?: JsonSchema | null; height?: number; readOnly?: boolean; confirmLeave?: boolean }>(),
  { schema: null, height: 360, readOnly: false, confirmLeave: true },
);
const emit = defineEmits<{
  (e: 'update:modelValue', v: Record<string, unknown>): void;
  (e: 'diagnostics', v: Diagnostic[]): void;
}>();

// Loaded only on desktop, and only when this component actually mounts there.
const Core = defineAsyncComponent(() => import('./internal/JsonEditorCore.vue'));

const core = ref<InstanceType<typeof Core> | null>(null);
const issues = ref<Diagnostic[]>([]);
const dirty = ref(false);
const blockedSwitch = ref(false);

const errorCount = computed(() => issues.value.filter((i) => i.severity === 'error').length);
const hasSyntaxError = computed(() => issues.value.some((i) => i.keyword === 'syntax'));

function onModel(v: Record<string, unknown>): void {
  emit('update:modelValue', v);
}

function format(): void {
  core.value?.format();
}

/**
 * Leaving the JSON view with an unparsed buffer is the one place the old implementation quietly
 * dropped work: it cleared the error and submitted the stale model. Ask instead.
 */
function requestLeave(): boolean {
  if (!props.confirmLeave || !dirty.value || !hasSyntaxError.value) return true;
  blockedSwitch.value = true;
  return false;
}

function discardAndLeave(): void {
  blockedSwitch.value = false;
  dirty.value = false;
  emit('update:modelValue', props.modelValue);
}

function stay(): void {
  blockedSwitch.value = false;
}

watch(issues, (v) => emit('diagnostics', v));

defineExpose({ format, requestLeave, blockedSwitch, issues });
</script>

<template>
  <div class="fje">
    <template v-if="isDesktop && !readOnly">
      <FuiNotice v-if="blockedSwitch" tone="warning" :text="translate('jsonEditor.lostOnSwitch')" />
      <Core
        v-if="!blockedSwitch"
        ref="core"
        :model-value="modelValue"
        :schema="schema"
        :height="height"
        :read-only="readOnly"
        @update:model-value="onModel"
        @diagnostics="issues = $event"
        @dirty="dirty = $event"
      />
      <div class="fje__tools">
        <FuiButton size="small" :label="translate('jsonEditor.format')" :icon="''" @click="format" />
        <FuiStatusTag v-if="errorCount" :tone="'danger'" :label="translate('jsonEditor.issuesCount', { n: errorCount })" />
        <FuiStatusTag v-else tone="success" :label="translate('jsonEditor.noIssues')" />
      </div>
      <ul v-if="issues.length" class="fje__issues">
        <li v-for="(d, i) in issues.slice(0, 20)" :key="i">
          <button class="fje__issue" @click="core?.reveal(d.from)">
            <span class="fje__path">{{ d.path || '/' }}</span>
            <span class="fje__msg">{{ translate(d.messageKey, d.params) }}</span>
          </button>
        </li>
        <li v-if="issues.length > 20" class="fje__more">{{ translate('jsonEditor.moreIssues', { n: issues.length - 20 }) }}</li>
      </ul>
      <div v-if="blockedSwitch" class="fje__blocked">
        <FuiButton :label="translate('jsonEditor.keepEditing')" @click="stay" />
        <FuiButton variant="danger" :label="translate('jsonEditor.discardAndSwitch')" @click="discardAndLeave" />
      </div>
    </template>
    <template v-else>
      <FuiReadOnlyViewer :content="modelValue" format="json" />
      <p class="fje__muted">{{ translate('manual.desktopJsonSoon') }}</p>
    </template>
  </div>
</template>

<style scoped>
.fje { display: flex; flex-direction: column; gap: var(--fui-sp-2); }
.fje__tools { display: flex; align-items: center; gap: 8px; }
.fje__issues { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; max-height: 160px; overflow: auto; }
.fje__issue { display: flex; gap: 8px; width: 100%; text-align: left; min-height: var(--fui-tap-min); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); padding: 6px 8px; font-size: var(--fui-fs-xs); cursor: pointer; }
.fje__path { font-family: ui-monospace, monospace; color: var(--fui-danger); flex: none; max-width: 45%; overflow: hidden; text-overflow: ellipsis; }
.fje__msg { color: var(--fui-text-secondary); }
.fje__more { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); padding: 4px 8px; }
.fje__blocked { display: flex; gap: 8px; }
.fje__muted { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); text-align: center; }
</style>
