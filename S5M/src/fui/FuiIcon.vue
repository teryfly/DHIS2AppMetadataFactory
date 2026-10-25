<script setup lang="ts">
import { computed, useAttrs } from 'vue';
import { Icon as VanIcon } from 'vant';
import 'vant/es/icon/style';

// Icon glyph (FUI answer to `<van-icon>`, UM-TECH-001). A decorative glyph is hidden from the
// accessibility tree; a clickable one becomes a real button role so it stays keyboard-reachable
// (UM-UX-002), and then it must also carry a name — `label` is required in practice by the
// aria-command-name audit, since the glyph itself has no text.
defineProps<{ label?: string }>();
const attrs = useAttrs();
const interactive = computed(() => 'onClick' in attrs || 'onKeyup' in attrs);

function activate(e: KeyboardEvent): void {
  // role="button" only promises activation; Vant's glyph is an <i>, so synthesise the click that
  // the fall-through @click listener is bound to.
  (e.target as HTMLElement).click();
}
</script>

<template>
  <VanIcon
    class="fui-icon"
    :role="interactive ? 'button' : undefined"
    :tabindex="interactive ? 0 : undefined"
    :aria-label="interactive ? label : undefined"
    :aria-hidden="interactive ? undefined : 'true'"
    @keydown.enter.prevent="activate"
    @keydown.space.prevent="activate"
  >
    <!-- a glyph you can press often wants a word beside it, inside the same target: a bare question
         mark reads as decoration, and two adjacent click targets for one action is one too many -->
    <slot />
  </VanIcon>
</template>

<style scoped>
.fui-icon { min-width: 16px; }
/* A glyph you can press is a control, and UM-UX-001 puts its target at 44 × 44: the box grows to
   that, the glyph keeps the size the layout asked for. Decorative icons keep their own box — there
   is nothing to hit on them. */
.fui-icon[role='button'] {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: var(--fui-tap-min);
  min-height: var(--fui-tap-min);
}
</style>
