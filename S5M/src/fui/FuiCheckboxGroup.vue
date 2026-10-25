<script setup lang="ts">
import { CheckboxGroup as VanCheckboxGroup } from 'vant';
import 'vant/es/checkbox/style';

// Multi-choice group (FUI answer to `<van-checkbox-group>`). `v-model` and `direction` fall
// through to Vant (UM-TECH-001).
</script>

<template>
  <VanCheckboxGroup class="fui-checkbox-group" role="group">
    <slot />
  </VanCheckboxGroup>
</template>

<style scoped>
.fui-checkbox-group { display: flex; flex-direction: column; gap: var(--fui-sp-2); }
</style>
