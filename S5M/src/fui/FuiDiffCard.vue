<script setup lang="ts">
const props = defineProps<{ field: string; oldValue?: unknown; newValue?: unknown }>();
const fmt = (v: unknown): string => (v == null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
void props;
</script>

<template>
  <div class="fui-diff">
    <div class="fui-diff__field">{{ field }}</div>
    <div class="fui-diff__vals">
      <span class="fui-diff__old">{{ fmt(oldValue) }}</span>
      <span class="fui-diff__arrow" aria-hidden="true">→</span>
      <span class="fui-diff__new">{{ fmt(newValue) }}</span>
    </div>
  </div>
</template>

<style scoped>
.fui-diff { padding: var(--fui-sp-2) 0; border-bottom: 1px dashed var(--fui-border); }
.fui-diff__field { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.fui-diff__vals { display: flex; align-items: center; gap: 8px; font-size: var(--fui-fs-sm); flex-wrap: wrap; }
.fui-diff__old { color: var(--fui-danger); text-decoration: line-through; }
.fui-diff__new { color: var(--fui-success); font-weight: 600; }
.fui-diff__arrow { color: var(--fui-text-tertiary); }
</style>
