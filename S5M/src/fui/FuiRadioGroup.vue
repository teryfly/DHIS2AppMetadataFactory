<script setup lang="ts">
import { RadioGroup as VanRadioGroup } from 'vant';
import 'vant/es/radio/style';
import 'vant/es/cell/style';

// Single-choice group (FUI answer to `<van-radio-group>`). `v-model`, `direction` and
// `@update:model-value` fall through to Vant.
</script>

<template>
  <VanRadioGroup class="fui-radio-group" role="radiogroup">
    <slot />
  </VanRadioGroup>
</template>

<style scoped>
.fui-radio-group :deep(.van-radio__label) { color: var(--fui-text); }
</style>
