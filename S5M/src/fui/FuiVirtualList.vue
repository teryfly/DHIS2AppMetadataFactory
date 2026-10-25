<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

// Windowed list. `FuiList` (Vant List) is infinite scroll — it still creates a DOM node per item,
// so a 5,000-object graph would mount 5,000 nodes. UM-REV-006 requires virtual scrolling at that
// size, and adding a dependency for fixed-row-height windowing was not worth it.
const props = withDefaults(
  defineProps<{ items: readonly unknown[]; rowHeight?: number; height?: number; buffer?: number }>(),
  { rowHeight: 48, height: 420, buffer: 6 },
);

const container = ref<HTMLElement | null>(null);
const scrollTop = ref(0);

const total = computed(() => props.items.length * props.rowHeight);
const start = computed(() => Math.max(0, Math.floor(scrollTop.value / props.rowHeight) - props.buffer));
const end = computed(() => Math.min(props.items.length, Math.ceil((scrollTop.value + props.height) / props.rowHeight) + props.buffer));
const slice = computed(() => props.items.slice(start.value, end.value));

function onScroll(e: Event): void {
  scrollTop.value = (e.target as HTMLElement).scrollTop;
}

// Keyboard scrolling inside a scroll container is native; this is only to keep the container
// focusable so a keyboard reader can reach the list at all (UM-UX-002).
onMounted(() => container.value?.addEventListener('scroll', onScroll, { passive: true }));
onBeforeUnmount(() => container.value?.removeEventListener('scroll', onScroll));
</script>

<template>
  <div ref="container" class="fui-vlist" :style="{ height: height + 'px' }" tabindex="0">
    <div class="fui-vlist__spacer" :style="{ height: total + 'px' }">
      <div class="fui-vlist__window" :style="{ transform: `translateY(${start * rowHeight}px)` }">
        <div v-for="(item, i) in slice" :key="start + i" class="fui-vlist__row" :style="{ height: rowHeight + 'px' }">
          <slot :item="item" :index="start + i" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fui-vlist { overflow: auto; -webkit-overflow-scrolling: touch; }
.fui-vlist:focus-visible { outline: 2px solid var(--fui-primary); }
.fui-vlist__spacer { position: relative; }
.fui-vlist__window { position: absolute; inset: 0 0 auto 0; will-change: transform; }
.fui-vlist__row { display: flex; align-items: center; }
</style>
