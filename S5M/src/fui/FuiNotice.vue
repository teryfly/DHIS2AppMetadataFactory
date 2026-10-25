<script setup lang="ts">
import { computed } from 'vue';
import { NoticeBar } from 'vant';
import 'vant/es/notice-bar/style';

const props = withDefaults(defineProps<{ tone?: 'warning' | 'info' | 'danger'; text?: string }>(), { tone: 'warning', text: '' });

const style = computed(() => {
  switch (props.tone) {
    case 'danger':
      return { color: 'var(--fui-danger)', background: 'var(--fui-danger-bg)' };
    case 'info':
      return { color: 'var(--fui-info)', background: 'var(--fui-info-bg)' };
    default:
      return { color: 'var(--fui-warning)', background: 'var(--fui-warning-bg)' };
  }
});
</script>

<template>
  <NoticeBar wrapable class="fui-notice" :color="style.color" :background="style.background">
    <span class="fui-notice__glyph" aria-hidden="true">⚠</span>
    <slot>{{ text }}</slot>
  </NoticeBar>
</template>

<style scoped>
.fui-notice { font-size: var(--fui-fs-sm); }
.fui-notice__glyph { margin-right: 6px; }
</style>
