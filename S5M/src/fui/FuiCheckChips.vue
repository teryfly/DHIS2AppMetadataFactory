<script setup lang="ts">
import type { ChipOption } from './types';

// Any-number-of choices drawn as chips: the multi-select sibling of FuiSegmented, for the two places
// that genuinely collect a set (a policy relaxation's data classes, a commit window's weekdays).
// The parent owns the order and the rules — this only reports the set the tap leaves behind.
const props = withDefaults(
  defineProps<{
    options: readonly ChipOption[];
    modelValue: readonly string[];
    label?: string;
    disabled?: boolean;
    small?: boolean;
  }>(),
  { label: '', disabled: false, small: false },
);

const emit = defineEmits<{ (e: 'update:modelValue', v: string[]): void }>();

function toggle(v: string): void {
  if (props.disabled) return;
  const picked = props.modelValue.includes(v)
    ? props.modelValue.filter((x) => x !== v)
    : [...props.modelValue, v];
  emit('update:modelValue', [...picked]);
}
</script>

<template>
  <div class="fui-chips" role="group" :aria-label="label || undefined">
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      class="fui-chips__chip"
      :class="{ 'fui-chips__chip--on': modelValue.includes(opt.value), 'fui-chips__chip--sm': small }"
      :aria-pressed="modelValue.includes(opt.value)"
      :disabled="disabled"
      @click="toggle(opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped src="./chip.css"></style>
