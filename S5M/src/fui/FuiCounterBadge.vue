<script setup lang="ts">
import { Badge as VanBadge } from 'vant';
import 'vant/es/badge/style';

// Count or dot marker on another element (FUI answer to `<van-badge>`, UM-TECH-001). `content`
// falls through; wrap the anchor element in the default slot.
</script>

<template>
  <VanBadge class="fui-counter-badge">
    <slot />
  </VanBadge>
</template>

<style scoped>
.fui-counter-badge { --van-badge-background-color: var(--fui-danger); }
</style>
