<script setup lang="ts">

import { Popup } from 'vant';
import 'vant/es/popup/style';
withDefaults(defineProps<{ modelValue: boolean; title?: string; round?: boolean }>(), { round: true, title: '' });
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();
function close(): void {
  emit('update:modelValue', false);
}
</script>

<!-- Secondary / confirmatory actions live in a thumb-reachable bottom panel (UM-P-02). -->
<template>
  <Popup :show="modelValue" position="bottom" :round="round" closeable :style="{ maxHeight: '82%' }" @update:show="(v: boolean) => emit('update:modelValue', v)" @click-close-icon="close">
    <div class="fui-sheet">
      <div class="fui-sheet__title">{{ title }}</div>
      <div class="fui-sheet__body"><slot /></div>
      <div v-if="$slots.footer" class="fui-sheet__footer"><slot name="footer" /></div>
    </div>
  </Popup>
</template>

<style scoped>
.fui-sheet { padding: var(--fui-sp-5) var(--fui-sp-4) calc(var(--fui-sp-4) + var(--fui-safe-bottom)); display: flex; flex-direction: column; min-height: 200px; }
.fui-sheet__title { font-size: var(--fui-fs-lg); font-weight: 600; margin-bottom: var(--fui-sp-4); }
.fui-sheet__body { flex: 1; overflow: auto; }
.fui-sheet__footer { margin-top: var(--fui-sp-4); }
</style>
