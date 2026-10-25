<script setup lang="ts">
import { Loading as VanLoading } from 'vant';
import 'vant/es/loading/style';

// Inline progress mark (FUI answer to `<van-loading>`, UM-TECH-001). `size` falls through.
</script>

<template>
  <VanLoading class="fui-spinner" />
</template>

<style scoped>
.fui-spinner { color: var(--fui-primary); }
</style>
