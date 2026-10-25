<script setup lang="ts">
import type { CheckItem } from './types';
withDefaults(defineProps<{ items: CheckItem[] }>(), {});
</script>

<!-- Green tick / red cross / grey pending (UM-DEP-002, UM-UX-005). A trailing action slot hosts
     small per-item buttons like "re-check drift". -->
<template>
  <ul class="fui-cl">
    <li v-for="it in items" :key="it.key" class="fui-cl__row">
      <span
        class="fui-cl__mark"
        :class="`fui-cl__mark--${it.state}`"
        :aria-label="it.state"
      >{{ it.state === 'pass' ? '✓' : it.state === 'fail' ? '✕' : '○' }}</span>
      <span class="fui-cl__label">{{ it.label }}</span>
      <span v-if="it.note" class="fui-cl__note">{{ it.note }}</span>
      <span class="fui-cl__action"><slot name="action" :item="it" /></span>
    </li>
  </ul>
</template>

<style scoped>
/* A row is a grid, not a flex line: on a 390 px phone the note is long and the action is short, and
   a flex line squeezes the button until Chinese text stacks one character per line. The mark spans
   both lines, the label and the action share the first, and the note gets the full text column. */
.fui-cl { list-style: none; margin: 0; padding: 0; display: grid; row-gap: var(--fui-sp-1); }
.fui-cl__row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) auto;
  align-items: center;
  column-gap: var(--fui-sp-2);
  row-gap: 2px;
  padding: var(--fui-sp-3) 0;
  border-bottom: 1px solid var(--fui-border);
  min-height: var(--fui-tap-min);
}
.fui-cl__mark { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; grid-area: 1 / 1 / 3 / 2; }
.fui-cl__mark--pass { color: var(--fui-success); background: var(--fui-success-bg); }
.fui-cl__mark--fail { color: var(--fui-danger); background: var(--fui-danger-bg); }
.fui-cl__mark--pending { color: var(--fui-muted); background: var(--fui-surface-2); }
.fui-cl__label { font-size: var(--fui-fs-sm); grid-area: 1 / 2; }
.fui-cl__note { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); grid-area: 2 / 2 / 3 / 4; }
.fui-cl__action { grid-area: 1 / 3; white-space: nowrap; }
</style>
