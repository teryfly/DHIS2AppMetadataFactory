<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string;
    subtitle?: string;
    /** first line is a bold conclusion (UM-P-03). */
    clickable?: boolean;
    flat?: boolean;
  }>(),
  { flat: false, title: '', subtitle: '' },
);
const emit = defineEmits<{ (e: 'click'): void }>();
</script>

<template>
  <div class="fui-card" :class="{ 'fui-card--flat': flat, 'fui-card--clickable': clickable }" @click="clickable && emit('click')">
    <div v-if="title || $slots.title || $slots.extra" class="fui-card__head">
      <div class="fui-card__titles">
        <div class="fui-card__title"><slot name="title">{{ title }}</slot></div>
        <div v-if="subtitle" class="fui-card__subtitle">{{ subtitle }}</div>
      </div>
      <div v-if="$slots.extra" class="fui-card__extra"><slot name="extra" /></div>
    </div>
    <div class="fui-card__body"><slot /></div>
  </div>
</template>

<style scoped>
.fui-card {
  background: var(--fui-surface);
  border-radius: var(--fui-radius-card);
  box-shadow: var(--fui-shadow-1);
  padding: var(--fui-sp-4);
  margin-bottom: var(--fui-sp-3);
}
.fui-card--flat { box-shadow: none; border: 1px solid var(--fui-border); }
.fui-card--clickable { cursor: pointer; transition: transform 0.08s ease; }
.fui-card--clickable:active { transform: scale(0.99); }
.fui-card__head { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--fui-sp-2); margin-bottom: var(--fui-sp-3); }
.fui-card__title { font-size: var(--fui-fs-lg); font-weight: 600; }
.fui-card__subtitle { color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); margin-top: 2px; }
.fui-card__body { color: var(--fui-text); }
</style>
