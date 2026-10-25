<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{ label: string; tone?: 'success' | 'danger' | 'warning' | 'info' | 'muted' | 'primary' }>(),
  { tone: 'muted' },
);

// Colour is never the only signal (UM-UX-005) — every tone has a distinct glyph too.
const glyph = computed(
  () =>
    ({ success: '✓', danger: '✕', warning: '!', info: 'i', primary: '●', muted: '—' })[props.tone],
);
</script>

<template>
  <span class="fui-tag" :class="`fui-tag--${tone}`">
    <span class="fui-tag__glyph" aria-hidden="true">{{ glyph }}</span>
    <span class="fui-tag__text">{{ label }}</span>
  </span>
</template>

<style scoped>
.fui-tag { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: var(--fui-radius-pill); font-size: var(--fui-fs-xs); font-weight: 500; line-height: 1.6; }
.fui-tag__glyph { font-weight: 700; }
.fui-tag--success { color: var(--fui-success); background: var(--fui-success-bg); }
.fui-tag--danger { color: var(--fui-danger); background: var(--fui-danger-bg); }
.fui-tag--warning { color: var(--fui-warning); background: var(--fui-warning-bg); }
.fui-tag--info { color: var(--fui-info); background: var(--fui-info-bg); }
.fui-tag--primary { color: var(--fui-primary); background: var(--fui-info-bg); }
.fui-tag--muted { color: var(--fui-text-secondary); background: var(--fui-surface-2); }
</style>
