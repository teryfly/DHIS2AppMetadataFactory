<script setup lang="ts">
import { computed, ref } from 'vue';
import { copyText } from '@/lib/clipboard';

const props = withDefaults(defineProps<{ content?: string | Record<string, unknown> | unknown[] | null; maxLines?: number; format?: 'json' | 'text' }>(), { maxLines: 500, format: 'text', content: null });

const text = computed(() => {
  const c = props.content;
  if (c == null) return '';
  if (typeof c === 'string') return c;
  try {
    return JSON.stringify(c, null, 2);
  } catch {
    return String(c);
  }
});
const lines = computed(() => text.value.split('\n'));
const expanded = ref(false);
const shown = computed(() => {
  const limit = props.maxLines;
  if (expanded.value || lines.value.length <= limit) return lines.value.join('\n');
  return lines.value.slice(0, limit).join('\n');
});
const truncated = computed(() => lines.value.length > props.maxLines && !expanded.value);
</script>

<!-- Raw content is read-only on mobile; large files show the first N lines and point to desktop
     (UM-REV-001). Copy is always available. -->
<template>
  <div class="fui-raw">
    <div class="fui-raw__bar">
      <span class="fui-raw__count">{{ $t('common.lines', { n: lines.length }) }}</span>
      <!-- It copies the text on screen, not an address: the artifact page next to it carries a real
           "copy link" button, and two identically named controls doing different things on one
           screen is what a screen reader (and a strict-mode test) trips over. -->
      <button class="fui-raw__copy" @click="copyText(text)">{{ $t('common.copyContent') }}</button>
    </div>
    <!-- max-height + overflow makes this a scrollable region, which keyboard users can only reach
         if it is focusable (WCAG 2.1.1, axe scrollable-region-focusable). -->
    <pre class="fui-raw__pre" role="region" tabindex="0" :aria-label="$t('review.content')">{{ shown }}</pre>
    <div v-if="truncated" class="fui-raw__trunc">
      <span>{{ $t('review.truncated') }}</span>
      <button class="fui-raw__expand" @click="expanded = true">{{ $t('common.viewAllAnyway') }}</button>
    </div>
  </div>
</template>

<style scoped>
.fui-raw { border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); overflow: hidden; background: var(--fui-surface-2); }
.fui-raw__bar { display: flex; justify-content: space-between; align-items: center; padding: 6px 12px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); border-bottom: 1px solid var(--fui-border); }
.fui-raw__copy { border: 0; background: none; color: var(--fui-primary); font-size: var(--fui-fs-xs); min-height: var(--fui-tap-min); }
.fui-raw__pre { margin: 0; padding: 12px; overflow: auto; font-size: var(--fui-fs-xs); line-height: 1.5; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre; max-height: 60vh; }
.fui-raw__trunc { padding: 8px 12px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--fui-border); }
.fui-raw__expand { border: 0; background: none; color: var(--fui-primary); min-height: var(--fui-tap-min); }
</style>
