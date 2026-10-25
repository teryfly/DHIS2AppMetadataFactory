<script setup lang="ts">
import { computed } from 'vue';
import type { TimelineItem } from './types';

const props = withDefaults(defineProps<{ items: TimelineItem[]; expandedKey?: string | null; showIndex?: boolean }>(), {
  expandedKey: null,
  showIndex: false,
});
const emit = defineEmits<{ (e: 'select', key: string): void }>();

const colorFor = (s: TimelineItem['status']): string =>
  ({
    done: 'var(--fui-success)',
    current: 'var(--fui-primary)',
    pending: 'var(--fui-muted)',
    failed: 'var(--fui-danger)',
    blocked: 'var(--fui-danger)',
    skipped: 'var(--fui-muted)',
  })[s];

const glyphFor = (s: TimelineItem['status']): string =>
  ({ done: '✓', current: '●', pending: '', failed: '✕', blocked: '!', skipped: '–' })[s];

const active = computed(() => props.expandedKey);
</script>

<template>
  <ol class="fui-tl">
    <li
      v-for="(it, i) in props.items"
      :key="it.key"
      class="fui-tl__item"
      :class="{ 'fui-tl__item--current': it.status === 'current' }"
      @click="emit('select', it.key)"
    >
      <div class="fui-tl__gutter">
        <span class="fui-tl__dot" :style="{ borderColor: colorFor(it.status), color: colorFor(it.status) }">
          {{ glyphFor(it.status) }}
        </span>
        <span v-if="i < props.items.length - 1" class="fui-tl__line" />
      </div>
      <div class="fui-tl__content">
        <div class="fui-tl__head">
          <span class="fui-tl__idx" v-if="showIndex">{{ i + 1 }}</span>
          <span class="fui-tl__title" :class="{ 'fui-tl__title--skipped': it.status === 'skipped' }">{{ it.title }}</span>
          <div v-if="$slots.action" class="fui-tl__action" @click.stop>
            <slot name="action" :item="it" />
          </div>
        </div>
        <div v-if="it.subtitle" class="fui-tl__sub">{{ it.subtitle }}</div>
        <div v-if="it.key === active" class="fui-tl__detail"><slot name="detail" :item="it" /></div>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.fui-tl { list-style: none; margin: 0; padding: 0; }
.fui-tl__item { display: flex; gap: var(--fui-sp-3); }
.fui-tl__gutter { display: flex; flex-direction: column; align-items: center; width: 22px; flex: none; }
.fui-tl__dot { width: 22px; height: 22px; border: 2px solid; border-radius: 50%; background: var(--fui-surface); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
.fui-tl__item--current .fui-tl__dot { box-shadow: 0 0 0 4px var(--fui-info-bg); }
.fui-tl__line { flex: 1; width: 2px; background: var(--fui-border); margin: 2px 0; }
.fui-tl__content { flex: 1; padding-bottom: var(--fui-sp-4); cursor: pointer; }
.fui-tl__head { display: flex; align-items: center; gap: 8px; }
.fui-tl__action { margin-left: auto; flex: none; }
.fui-tl__idx { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); }
.fui-tl__title { font-weight: 600; font-size: var(--fui-fs-md); }
.fui-tl__title--skipped { color: var(--fui-text-tertiary); font-weight: 400; }
.fui-tl__sub { color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); margin-top: 2px; }
.fui-tl__detail { margin-top: var(--fui-sp-3); }
</style>
