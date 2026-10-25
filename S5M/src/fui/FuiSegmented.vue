<script setup lang="ts" generic="T extends string">
// Single choice drawn as chips — the phone's equivalent of a segmented control (UM-TECH-001: eight
// screens each carried their own copy of this markup). `role="group"` plus `aria-pressed` says what
// the markup actually behaves like: every chip is a tabbable button and a tap selects it. Borrowing
// radio semantics would promise arrow-key navigation and a roving focus this control does not
// implement, which is a worse promise than a plainer one.
//
// The value is generic because the choices are enums in S1's contract, not free strings: a screen
// that stores `'ACCEPT' | 'FIX'` should read and write that union without a cast reminding nobody
// when the third disposition appears.
const model = defineModel<T>({ required: true });

const props = withDefaults(
  defineProps<{
    options: readonly { value: T; label: string }[];
    /** Names the group for a screen reader; the visible legend stays the page's own line. */
    label?: string;
    disabled?: boolean;
    /** The compact size, for a chip row that sits inside another control. */
    small?: boolean;
  }>(),
  { label: '', disabled: false, small: false },
);

function pick(v: T): void {
  if (props.disabled) return;
  model.value = v;
}
</script>

<template>
  <div class="fui-chips" role="group" :aria-label="label || undefined">
    <button
      v-for="opt in props.options"
      :key="opt.value"
      type="button"
      class="fui-chips__chip"
      :class="{ 'fui-chips__chip--on': opt.value === model, 'fui-chips__chip--sm': small }"
      :aria-pressed="opt.value === model"
      :disabled="disabled"
      @click="pick(opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped src="./chip.css"></style>
