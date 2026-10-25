<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';
import { FuiGraphViewer } from '@/fui';
import { makeGraphFixture } from '@/lib/graph/fixtures';
import { startFrameSampler, type FpsSample } from '@/lib/graph/fps';

// DEV-only UM-CAP-002 harness (?n=1000&renderer=canvas). Playwright drives real pan/pinch gestures
// over this page and reads the sampled frame intervals; nothing here ships in a normal build.
const route = useRoute();
const n = computed(() => Math.max(1, Math.min(5000, Number(route.query.n ?? 1000))));
const renderer = computed<'auto' | 'canvas' | 'svg'>(() => (['auto', 'canvas', 'svg'].includes(String(route.query.renderer)) ? String(route.query.renderer) : 'auto') as 'auto' | 'canvas' | 'svg');

const viewer = ref<InstanceType<typeof FuiGraphViewer> | null>(null);
const fixture = computed(() => makeGraphFixture({ nodes: n.value, edgesPerNode: 2 }));
const height = 460;

const sampler = ref<{ stop: () => FpsSample } | null>(null);
const last = ref<FpsSample | null>(null);
const selected = ref('');

function begin(): void {
  sampler.value = startFrameSampler();
}
function finish(): void {
  if (sampler.value) last.value = sampler.value.stop();
  sampler.value = null;
}

onMounted(() => {
  // The fixture for n=5,000 is built synchronously during setup, so the viewer exists by mount;
  // expanding is retraced on the next frame because the first paint of 5,000 nodes can swallow a
  // 50 ms timer, and a silently-unexpanded "expanded" row is the exact fake reading this harness
  // was built to avoid.
  if (route.query.expand === '1') {
    requestAnimationFrame(() => requestAnimationFrame(() => viewer.value?.expandAll()));
  }
  (window as unknown as { __s5mFinish?: () => void }).__s5mFinish = finish;
  (window as unknown as { __s5mDraws?: () => unknown }).__s5mDraws = () => ({ draws: viewer.value?.drawCount?.() ?? -1, view: viewer.value?.viewState?.() ?? null, pointers: viewer.value?.pointerCount?.() ?? -1, canvasMode: viewer.value?.canvasMode?.() ?? null });
  (window as unknown as { __s5mSample?: () => FpsSample | null }).__s5mSample = () => last.value;
  (window as unknown as { __s5mStats?: () => { loose: number; groups: number } | null }).__s5mStats = () =>
    (viewer.value?.stats?.() ?? null) as { loose: number; groups: number } | null;
});

defineExpose({ begin, finish, last });
(window as unknown as { __s5mBegin?: () => void }).__s5mBegin = begin;
</script>

<template>
  <div class="gb" data-testid="graph-bench" :data-n="n" :data-renderer="renderer">
    <FuiGraphViewer
      ref="viewer"
      :nodes="fixture.nodes"
      :edges="fixture.edges"
      :orphans="fixture.orphans"
      :unresolved="fixture.unresolved.map((r) => r.fromBusinessKey)"
      :selected-key="selected"
      :height="height"
      :renderer="renderer"
      @select="selected = $event"
    />
    <pre v-if="last" class="gb__out" data-testid="bench-sample">{{ JSON.stringify(last) }}</pre>
  </div>
</template>

<style scoped>
.gb { padding: var(--fui-sp-2); }
.gb__out { font-size: 10px; background: var(--fui-surface-2); padding: 6px; }
</style>
