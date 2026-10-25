<script setup lang="ts">
import { ref } from 'vue';
import FuiStatusTag from './FuiStatusTag.vue';

withDefaults(
  defineProps<{
    tone?: 'success' | 'danger' | 'warning' | 'info' | 'muted' | 'primary';
    statusText?: string;
    /** The headline conclusion — what happens / what this is (UM-P-03). */
    conclusion?: string;
    impact?: string;
    produce?: string;
  }>(),
  { tone: 'info', statusText: '', conclusion: '', impact: '', produce: '' },
);

const expanded = ref(false);
</script>

<template>
  <section class="fui-conclusion" :class="`fui-conclusion--${tone}`">
    <div class="fui-conclusion__top">
      <FuiStatusTag v-if="statusText" :tone="tone" :label="statusText" />
      <slot name="status" />
    </div>
    <h2 class="fui-conclusion__headline">{{ conclusion }}</h2>
    <ul v-if="impact || produce" class="fui-conclusion__points">
      <li v-if="impact"><span class="fui-conclusion__k">{{ impact }}</span></li>
      <li v-if="produce"><span class="fui-conclusion__k">{{ produce }}</span></li>
    </ul>
    <slot name="summary" />
    <div v-if="$slots.details" class="fui-conclusion__details">
      <button class="fui-conclusion__toggle" @click="expanded = !expanded" :aria-expanded="expanded">
        {{ expanded ? $t('common.collapse') : $t('common.expandDetails') }} <span aria-hidden="true">{{ expanded ? '▲' : '▼' }}</span>
      </button>
      <div v-show="expanded" class="fui-conclusion__body"><slot name="details" /></div>
    </div>
  </section>
</template>

<style scoped>
.fui-conclusion { background: var(--fui-surface); border-radius: var(--fui-radius-card); box-shadow: var(--fui-shadow-1); padding: var(--fui-sp-4); margin-bottom: var(--fui-sp-3); border-left: 4px solid var(--fui-info); }
.fui-conclusion--success { border-left-color: var(--fui-success); }
.fui-conclusion--danger { border-left-color: var(--fui-danger); }
.fui-conclusion--warning { border-left-color: var(--fui-warning); }
.fui-conclusion--primary { border-left-color: var(--fui-primary); }
.fui-conclusion--muted { border-left-color: var(--fui-muted); }
.fui-conclusion__top { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.fui-conclusion__headline { margin: 0 0 8px; font-size: var(--fui-fs-lg); font-weight: 700; line-height: 1.4; }
.fui-conclusion__points { margin: 0; padding: 0; list-style: none; }
.fui-conclusion__points li { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); padding: 2px 0; }
.fui-conclusion__details { margin-top: 12px; border-top: 1px solid var(--fui-border); padding-top: 8px; }
.fui-conclusion__toggle { background: none; border: 0; color: var(--fui-primary); font-size: var(--fui-fs-sm); padding: 6px 0; min-height: var(--fui-tap-min); cursor: pointer; }
.fui-conclusion__body { margin-top: 8px; }
</style>
