<script setup lang="ts">
import { Radio as VanRadio } from 'vant';
import 'vant/es/radio/style';

// One choice inside a FuiRadioGroup. `name`, the visible label (default slot) and `shape` pass
// through to Vant (UM-TECH-001).
</script>

<template>
  <VanRadio class="fui-radio">
    <slot />
  </VanRadio>
</template>

<style scoped>
.fui-radio { min-height: var(--fui-tap-min); align-items: center; }
</style>
