<script setup lang="ts">

import { SwipeCell } from 'vant';
import 'vant/es/swipe-cell/style';
withDefaults(defineProps<{ rightWidth?: string }>(), { rightWidth: '88px' });
</script>

<!-- Left-swipe reveals quick actions; every gesture has a tappable equivalent (UM-UX-002). -->
<template>
  <SwipeCell :right-width="rightWidth" class="fui-swipe-cell">
    <slot />
    <template #right><slot name="right" /></template>
  </SwipeCell>
</template>

<style scoped>
.fui-swipe-cell { background: transparent; }
</style>
