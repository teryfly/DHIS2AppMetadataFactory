<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { EditorState, type Extension } from '@codemirror/state';
import { EditorView, keymap, drawSelection, highlightActiveLine, highlightSpecialChars, rectangularSelection } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { bracketMatching, foldGutter, foldKeymap, indentOnInput, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language';
import { json } from '@codemirror/lang-json';
import { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { lintGutter, linter, lintKeymap, type Diagnostic as CmDiagnostic } from '@codemirror/lint';
import { diagnose, type Diagnostic } from '@/lib/json/sectionValidator';
import { schemaCompletion } from '@/lib/json/schemaCompletion';
import { formatEdits } from '@/lib/json/offsetPointer';
import type { JsonSchema } from '@/fui/schemaTypes';
import { t as translate } from '@/lib/labels';

/**
 * The only file in the repository that may import CodeMirror (UM-TECH-001/UM-TECH-002). It is
 * reached exclusively through `FuiJsonEditor`, which loads it dynamically so the editor and its
 * validators never enter the first-screen graph.
 */
const props = withDefaults(
  defineProps<{ modelValue: Record<string, unknown>; schema?: JsonSchema | null; height?: number; readOnly?: boolean; lintDelayMs?: number }>(),
  { schema: null, height: 360, readOnly: false, lintDelayMs: 250 },
);
const emit = defineEmits<{
  (e: 'update:modelValue', v: Record<string, unknown>): void;
  (e: 'diagnostics', v: Diagnostic[]): void;
  (e: 'dirty', v: boolean): void;
}>();

const host = ref<HTMLElement | null>(null);
const view = shallowRef<EditorView | null>(null);
const issues = ref<Diagnostic[]>([]);
const validating = ref(false);

// Nothing here parses JSON or runs Ajv on the keystroke path: `linter({ delay })` schedules the
// source after typing pauses, and an unchanged document reuses the previous result so a scroll or
// a selection change cannot trigger a re-validation of 5,000 lines.
let lastDiagnostics: CmDiagnostic[] = [];
let docSignature = '';
let docLength = -1;

function runDiagnosis(v: EditorView): CmDiagnostic[] {
  const text = v.state.doc.toString();
  if (text.length === docLength && text === docSignature) return lastDiagnostics;
  docLength = text.length;
  docSignature = text;
  validating.value = true;
  const result = diagnose(props.schema, text);
  issues.value = result.diagnostics;
  emit('diagnostics', result.diagnostics);
  validating.value = false;
  if (!result.blockedBySyntax && result.parsed && typeof result.parsed === 'object' && !Array.isArray(result.parsed)) {
    lastEmitted = result.parsed;
    emit('update:modelValue', result.parsed as Record<string, unknown>);
  }
  lastDiagnostics = result.diagnostics.map(
    (d) =>
      ({
        from: d.from,
        to: Math.max(d.to, d.from + 1),
        severity: d.severity,
        message: `${translate(d.messageKey, d.params)}${d.path && d.path !== '/' ? ` · ${d.path}` : ''}`,
      }) as CmDiagnostic,
  );
  return lastDiagnostics;
}

function label(key: string): string {
  return translate(`jsonEditor.${key}`);
}

const extensions: Extension[] = [
  history(),
  drawSelection(),
  highlightActiveLine(),
  highlightSpecialChars(),
  rectangularSelection(),
  indentOnInput(),
  bracketMatching(),
  foldGutter(),
  syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
  json(),
  closeBrackets(),
  autocompletion({
    override: [schemaCompletion(props.schema ?? null, { keyDetail: label('keyDetail'), requiredDetail: label('requiredDetail'), enumDetail: label('enumDetail') })],
    icons: false,
  }),
  lintGutter(),
  linter((v) => runDiagnosis(v), { delay: props.lintDelayMs }),
  keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...completionKeymap, ...foldKeymap, ...lintKeymap, indentWithTab]),
  EditorView.lineWrapping,
  EditorView.updateListener.of((u) => {
    if (u.docChanged) emit('dirty', true);
  }),
];

if (props.readOnly) extensions.push(EditorState.readOnly.of(true));

onMounted(() => {
  if (!host.value) return;
  const v = new EditorView({
    state: EditorState.create({ doc: JSON.stringify(props.modelValue ?? {}, null, 2), extensions }),
    parent: host.value,
  });
  view.value = v;
});

onBeforeUnmount(() => {
  view.value?.destroy();
  view.value = null;
});

/** The object we last handed back; used to recognise our own change coming back down as a prop. */
let lastEmitted: unknown = null;
/** True while this component is the one that wrote the buffer. */
let projecting = false;

// Model → text. Skipped when the model *is* what the editor just produced (that would reformat
// under the caret), and skipped while the buffer is dirty — the shell asks the reader instead.
watch(
  () => props.modelValue,
  (next) => {
    const v = view.value;
    if (!v || projecting) return;
    if (next === lastEmitted) return;
    const wanted = JSON.stringify(next ?? {}, null, 2);
    if (v.state.doc.toString() === wanted) return;
    projecting = true;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: wanted }, userEvent: 'model-sync' });
    window.setTimeout(() => {
      projecting = false;
    }, 0);
  },
);

/** 格式化 — applied as one ChangeSet so it undoes as a single step (UM-CAP-001). */
function format(): void {
  const v = view.value;
  if (!v) return;
  const edits = formatEdits(v.state.doc.toString(), 2);
  if (!edits.length) return;
  v.dispatch({ changes: edits.map((e) => ({ from: e.offset, to: e.offset + e.length, insert: e.content })), userEvent: 'format' });
}

/** Jump to an issue from the list the shell renders. */
function reveal(from: number): void {
  const v = view.value;
  if (!v) return;
  v.dispatch({ selection: { anchor: from }, scrollIntoView: true });
  v.focus();
}

defineExpose({ format, reveal, issues, validating });
</script>

<template>
  <div class="jec" :style="{ height: height + 'px' }">
    <div class="jec__bar">
      <span class="jec__count" :class="{ 'is-bad': issues.length > 0 }" aria-live="polite">
        {{ validating ? translate('jsonEditor.validating') : translate('jsonEditor.issuesCount', { n: issues.length }) }}
      </span>
      <span class="jec__hint">{{ translate('jsonEditor.formatHint') }}</span>
    </div>
    <div ref="host" class="jec__editor" />
  </div>
</template>

<style scoped>
.jec { display: flex; flex-direction: column; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); overflow: hidden; }
.jec__bar { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 4px 8px; border-bottom: 1px solid var(--fui-border); background: var(--fui-surface-2); font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.jec__count.is-bad { color: var(--fui-danger); }
.jec__editor { flex: 1; min-height: 0; overflow: auto; }
.jec__editor :deep(.cm-editor) { height: 100%; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: var(--fui-fs-sm); }
.jec__editor :deep(.cm-editor.cm-focused) { outline: 2px solid var(--fui-primary); outline-offset: -2px; }
.jec__editor :deep(.cm-gutters) { background: var(--fui-surface-2); border-right: 1px solid var(--fui-border); color: var(--fui-text-tertiary); }
.jec__editor :deep(.cm-content) { caret-color: var(--fui-text); }
</style>
