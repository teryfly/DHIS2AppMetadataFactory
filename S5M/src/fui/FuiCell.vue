<script setup lang="ts">
import { Cell as VanCell } from 'vant';
import 'vant/es/cell/style';

// A tappable list row (FUI answer to `<van-cell>`, UM-TECH-001). `title`, `is-link`, `label` and
// `@click` fall through.
</script>

<template>
  <VanCell class="fui-cell">
    <slot />
  </VanCell>
</template>

<style scoped>
.fui-cell { background: var(--fui-surface); min-height: var(--fui-tap-min); }
.fui-cell :deep(.van-cell__title) { color: var(--fui-text); }
</style>
