<script setup lang="ts">
import { computed } from 'vue';
const props = withDefaults(defineProps<{ percent?: number; size?: number }>(), { percent: 0, size: 56 });
const clamped = computed(() => Math.max(0, Math.min(100, Math.round(props.percent))));
const ring = computed(() => `conic-gradient(var(--fui-primary) ${clamped.value}%, var(--fui-surface-2) ${clamped.value}% 100%)`);
</script>

<template>
  <span
    class="fui-ring"
    :style="{ width: size + 'px', height: size + 'px', background: ring }"
    role="img"
    :aria-label="clamped + '%'"
  >
    <span class="fui-ring__inner"><span class="fui-ring__val">{{ clamped }}%</span></span>
  </span>
</template>

<style scoped>
.fui-ring { position: relative; display: inline-flex; border-radius: 50%; flex: none; }
.fui-ring__inner { position: absolute; inset: 5px; background: var(--fui-surface); border-radius: 50%; display: flex; align-items: center; justify-content: center; }
.fui-ring__val { font-size: var(--fui-fs-xs); font-weight: 600; color: var(--fui-text); }
</style>
