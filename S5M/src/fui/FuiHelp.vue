<script setup lang="ts">
/**
 * One field's consequence, behind a question mark (UM-UX-003 explains a concept where the operator
 * meets it, not in a page of its own).
 *
 * A form can state what a box is called and still leave the operator guessing what the value does.
 * The mobile sheet for a model registration had six such boxes and one paragraph trying to cover
 * them all, so the answer had to be reachable per item: the label stays short, and the cost of a
 * wrong value is one tap away.
 *
 * The text is a prop rather than a built-in string because `src/fui` carries no Chinese at all
 * (UM-AC-16) — the screen that knows what the field is supplies both the words and the accessible
 * name of the toggle.
 *
 * Reveal, not hover: a tooltip has no finger equivalent, and the same control has to read the same
 * way on a phone. `aria-controls` is given only while the panel exists, so a collapsed toggle never
 * points at an element that is not there.
 */
import { ref } from 'vue';
import FuiIcon from './FuiIcon.vue';

defineProps<{ text: string; label: string }>();

const open = ref(false);
const panelId = `fui-help-${Math.random().toString(36).slice(2, 8)}`;
</script>

<template>
  <div class="fui-help">
    <FuiIcon
      name="question-o"
      :label="label"
      :aria-expanded="open"
      :aria-controls="open ? panelId : undefined"
      @click="open = !open"
    >
      <span class="fui-help__cue">{{ label }}</span>
    </FuiIcon>
    <p v-if="open" :id="panelId" class="fui-help__body">{{ text }}</p>
  </div>
</template>

<style scoped>
.fui-help { display: flex; flex-direction: column; align-items: flex-start; margin: -2px 0 2px; color: var(--fui-text-tertiary); }
.fui-help__cue { margin-left: 4px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.fui-help__body { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); line-height: 1.5; }
</style>
