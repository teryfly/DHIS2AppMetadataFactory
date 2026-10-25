<script setup lang="ts">
import { computed } from 'vue';

// ArtifactDiffView.lines / DiffObjectView.changes are free-form objects (additionalProperties),
// so the renderer reads the common shapes defensively rather than assuming a fixed schema.
const props = defineProps<{ lines?: Record<string, unknown>[]; emptyHint?: string }>();

type Kind = 'add' | 'del' | 'ctx';
function kindOf(l: Record<string, unknown>): Kind {
  const raw = String(l.type ?? l.kind ?? l.op ?? l.marker ?? l.status ?? '').toLowerCase();
  if (l.added === true || raw.includes('add') || raw === '+') return 'add';
  if (l.removed === true || raw.includes('del') || raw.includes('remov') || raw === '-') return 'del';
  return 'ctx';
}
function textOf(l: Record<string, unknown>): string {
  const t = l.text ?? l.content ?? l.line ?? l.value;
  if (typeof t === 'string') return t;
  const left = l.left ?? l.old ?? l.before;
  const right = l.right ?? l.new ?? l.after;
  if (left != null || right != null) return `${String(left ?? '')} → ${String(right ?? '')}`;
  return JSON.stringify(l);
}
function fieldOf(l: Record<string, unknown>): string {
  return String(l.field ?? l.name ?? l.key ?? l.path ?? '');
}
const rows = computed(() => (props.lines ?? []).map((l) => ({ raw: l, kind: kindOf(l), text: textOf(l), field: fieldOf(l) })));
</script>

<template>
  <div class="fui-diff">
    <div v-if="!rows.length" class="fui-diff__empty">{{ emptyHint ?? $t('common.notFound') }}</div>
    <div v-for="(r, i) in rows" :key="i" class="fui-diff__line" :class="`fui-diff__line--${r.kind}`">
      <span class="fui-diff__sign" aria-hidden="true">{{ r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : '·' }}</span>
      <span v-if="r.field" class="fui-diff__field">{{ r.field }}</span>
      <span class="fui-diff__text">{{ r.text }}</span>
    </div>
  </div>
</template>

<style scoped>
.fui-diff { border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); overflow: hidden; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: var(--fui-fs-xs); }
.fui-diff__empty { padding: var(--fui-sp-4); text-align: center; color: var(--fui-text-tertiary); }
.fui-diff__line { display: flex; gap: 6px; padding: 2px 8px; align-items: baseline; white-space: pre-wrap; word-break: break-word; }
.fui-diff__line--add { background: var(--fui-success-bg); }
.fui-diff__line--del { background: var(--fui-danger-bg); }
.fui-diff__sign { width: 14px; flex: none; font-weight: 700; }
.fui-diff__line--add .fui-diff__sign { color: var(--fui-success); }
.fui-diff__line--del .fui-diff__sign { color: var(--fui-danger); }
.fui-diff__line--ctx .fui-diff__sign { color: var(--fui-text-tertiary); }
.fui-diff__field { color: var(--fui-info); }
</style>
