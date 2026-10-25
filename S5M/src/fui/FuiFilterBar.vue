<script setup lang="ts">
withDefaults(
  defineProps<{ modelValue: string; options: { value: string; label: string; count?: number }[] }>(),
  {},
);
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>();
</script>

<template>
  <div class="fui-filter" role="tablist">
    <button
      v-for="opt in options"
      :key="opt.value"
      class="fui-filter__chip"
      :class="{ 'fui-filter__chip--active': modelValue === opt.value }"
      role="tab"
      :aria-selected="modelValue === opt.value"
      @click="emit('update:modelValue', opt.value)"
    >
      {{ opt.label }}
      <span v-if="opt.count != null" class="fui-filter__count">{{ opt.count }}</span>
    </button>
  </div>
</template>

<style scoped>
.fui-filter { display: flex; gap: 8px; overflow-x: auto; padding: var(--fui-sp-2) var(--fui-sp-4); -webkit-overflow-scrolling: touch; }
.fui-filter__chip { flex: none; min-height: var(--fui-tap-min); padding: 0 14px; border-radius: var(--fui-radius-pill); border: 1px solid var(--fui-border); background: var(--fui-surface); color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); }
.fui-filter__chip--active { background: var(--fui-primary); color: var(--fui-on-primary); border-color: var(--fui-primary); }
.fui-filter__count { margin-left: 4px; opacity: 0.85; }
</style>
