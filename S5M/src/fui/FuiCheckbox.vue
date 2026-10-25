<script setup lang="ts">
import { Checkbox as VanCheckbox } from 'vant';
import 'vant/es/checkbox/style';

// One choice inside a FuiCheckboxGroup (UM-TECH-001).
</script>

<template>
  <VanCheckbox class="fui-checkbox">
    <slot />
  </VanCheckbox>
</template>

<style scoped>
.fui-checkbox { min-height: var(--fui-tap-min); align-items: center; }
</style>
