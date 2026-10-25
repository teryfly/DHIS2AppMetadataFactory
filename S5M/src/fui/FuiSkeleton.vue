<script setup lang="ts">
withDefaults(defineProps<{ loading?: boolean; rows?: number; title?: boolean }>(), { loading: true, rows: 3, title: true });
</script>

<!-- Skeleton instead of a full-page spinner (UM-NET-006). -->
<template>
  <div v-if="loading" class="fui-skeleton">
    <div v-if="title" class="fui-skeleton__title" />
    <div v-for="n in rows" :key="n" class="fui-skeleton__row" />
  </div>
  <slot v-else />
</template>

<style scoped>
.fui-skeleton { padding: var(--fui-sp-4) 0; }
.fui-skeleton__title, .fui-skeleton__row { background: var(--fui-surface-2); border-radius: 8px; animation: fui-shimmer 1.4s infinite; }
.fui-skeleton__title { height: 20px; width: 45%; margin-bottom: var(--fui-sp-3); }
.fui-skeleton__row { height: 14px; margin-bottom: var(--fui-sp-2); }
.fui-skeleton__row:nth-child(odd) { width: 90%; }
@keyframes fui-shimmer { 0% { opacity: 1; } 50% { opacity: 0.55; } 100% { opacity: 1; } }
</style>
