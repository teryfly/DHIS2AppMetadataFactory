<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import type { GraphEdgeView, GraphNodeView } from '@/api/types';
import FuiButton from './FuiButton.vue';
import { buildScene, HitGrid, neighbours, NODE_RADIUS, NodeFlag, type GraphScene } from '@/lib/graph/scene';
import { draw, type Palette } from '@/lib/graph/canvasRenderer';

/**
 * UM-CAP-002 dependency-graph viewer.
 *
 * Two renderers, chosen honestly (docs/adr/002): the hand-written SVG is kept as the
 * `prefers-reduced-motion` / no-Canvas fallback, and Canvas-2D is the default because pan/zoom has
 * to cost O(viewport) per frame and an SVG scene graph costs O(nodes).
 *
 * Interaction is per the requirement: two-finger pinch, drag the canvas, drag a node, tap a node to
 * highlight its adjacency and the reference field on each edge, search-locate on the reader's own hits,
 * collapse by object type above 200 nodes with per-group expand, and explicit styling for orphan
 * and unresolved-reference nodes.
 */
const props = withDefaults(
  defineProps<{
    nodes: GraphNodeView[];
    edges: GraphEdgeView[];
    selectedKey?: string;
    height?: number;
    /** Keys the reader's search hit; locates without pruning the model. */
    matches?: string[];
    /** S1's `orphans`: no edge touches them, on the complete build. */
    orphans?: string[];
    /** Business keys whose fields reference something the Registry does not know. */
    unresolved?: string[];
    interactive?: boolean;
    renderer?: 'auto' | 'canvas' | 'svg';
  }>(),
  { selectedKey: '', height: 460, matches: () => [], orphans: () => [], unresolved: () => [], interactive: true, renderer: 'auto' },
);
const emit = defineEmits<{ (e: 'select', businessKey: string): void }>();

const { t } = useI18n();
const host = ref<HTMLElement | null>(null);
const canvas = shallowRef<HTMLCanvasElement | null>(null);
const scene = shallowRef<GraphScene | null>(null);
const grid = shallowRef<HitGrid | null>(null);
const view = ref({ scale: 1, tx: 0, ty: 0 });
const expanded = ref<Set<string>>(new Set());
const reducedMotion = ref(false);

let raf = 0;
let draws = 0;
let dirty = true;
let ctx: CanvasRenderingContext2D | null = null;

const useCanvas = computed(() => {
  if (props.renderer === 'svg') return false;
  if (props.renderer === 'canvas') return true;
  return !reducedMotion.value && typeof window !== 'undefined' && !!document.createElement('canvas').getContext;
});

const selected = computed(() => {
  const s = scene.value;
  if (!s || !props.selectedKey) return -1;
  return s.keys.indexOf(props.selectedKey);
});

const highlight = computed<Set<number>>(() => {
  const s = scene.value;
  const out = new Set<number>();
  if (!s || selected.value < 0) return out;
  const n = neighbours(s, selected.value);
  for (const i of n.outbound) out.add(i);
  for (const i of n.inbound) out.add(i);
  return out;
});

const matchSet = computed(() => new Set(props.matches ?? []));

/** The reference fields on the selected node's edges — "高亮其邻接关系与引用字段". */
const selectedFields = computed<string[]>(() => {
  const s = scene.value;
  if (!s || selected.value < 0) return [];
  const set = new Set<string>();
  for (const e of s.edges) {
    if (e.from !== selected.value && e.to !== selected.value) continue;
    const f = s.fields[e.field];
    if (f) set.add(f);
  }
  return [...set];
});

function palette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    edge: v('--fui-muted', '#9aa3ae'),
    edgeHighlight: v('--fui-primary', '#1677ff'),
    node: v('--fui-primary', '#1677ff'),
    orphan: v('--fui-warning', '#fa8c16'),
    noUid: v('--fui-danger', '#d4380d'),
    unresolved: v('--fui-danger', '#d4380d'),
    selected: v('--fui-text', '#1f2328'),
    match: v('--fui-success', '#52c41a'),
    surface: v('--fui-on-primary', '#ffffff'),
    text: v('--fui-text-secondary', '#57606a'),
  };
}

function rebuild(): void {
  const s = buildScene(props.nodes, props.edges, {
    expandedTypes: expanded.value,
    orphans: new Set(props.orphans ?? []),
    unresolved: new Set(props.unresolved ?? []),
  });
  scene.value = s;
  grid.value = new HitGrid(s).build();
  markDirty();
}

function markDirty(): void {
  dirty = true;
}

function frame(): void {
  raf = requestAnimationFrame(frame);
  if (!dirty || !ctx || !scene.value) return;
  dirty = false;
  draws++;
  const el = canvas.value;
  if (!el) return;
  const cssW = el.clientWidth;
  const cssH = el.clientHeight;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  // Resize only when the box actually changed — resizing a canvas clears it and costs a frame.
  if (el.width !== Math.round(cssW * dpr) || el.height !== Math.round(cssH * dpr)) {
    el.width = Math.round(cssW * dpr);
    el.height = Math.round(cssH * dpr);
  }
  draw(ctx, scene.value, view.value, {
    selected: selected.value,
    highlight: highlight.value,
    matches: matchSet.value,
    palette: palette(),
    dpr,
    cssW,
    cssH,
    reducedMotion: reducedMotion.value,
  });
}

onMounted(() => {
  reducedMotion.value = !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (useCanvas.value && canvas.value) {
    ctx = canvas.value.getContext('2d');
    frame();
  }
  rebuild();
  fit();
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
});

watch(() => [props.nodes, props.edges, props.orphans, props.unresolved, expanded.value], rebuild, { deep: false });
watch(() => props.selectedKey, markDirty);
watch(matchSet, markDirty);
watch(view, () => { if (!useCanvas.value) draws++; }, { deep: true });

function worldPoint(clientX: number, clientY: number): { x: number; y: number } {
  const rect = host.value!.getBoundingClientRect();
  return { x: (clientX - rect.left - view.value.tx) / view.value.scale, y: (clientY - rect.top - view.value.ty) / view.value.scale };
}

/**
 * Pointer book. Two or more active pointers means pinch; one means pan, or drag when it landed on a
 * node. Coordinates go straight into `scene.pos`, outside Vue reactivity, so a drag is a typed-array
 * write plus one `markDirty()` rather than a reactive patch of thousands of vnodes.
 */
const pointers = new Map<number, { x: number; y: number }>();
let pinchStart = 0;
let pinchScaleStart = 1;
let draggingNode = -1;
let moved = 0;

function onPointerDown(e: PointerEvent): void {
  if (!props.interactive) return;
  (e.target as Element).setPointerCapture?.(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  moved = 0;
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    pinchStart = Math.hypot(a.x - b.x, a.y - b.y);
    pinchScaleStart = view.value.scale;
    draggingNode = -1;
    return;
  }
  const p = worldPoint(e.clientX, e.clientY);
  const hit = grid.value?.query(p.x, p.y, NODE_RADIUS / view.value.scale + 4) ?? -1;
  draggingNode = hit;
}

function onPointerMove(e: PointerEvent): void {
  if (!props.interactive || !pointers.has(e.pointerId)) return;
  const prev = pointers.get(e.pointerId)!;
  const dx = e.clientX - prev.x;
  const dy = e.clientY - prev.y;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  moved += Math.abs(dx) + Math.abs(dy);

  if (pointers.size >= 2 && pinchStart > 0) {
    const [a, b] = [...pointers.values()];
    const dist = Math.hypot(a.x - b.x, a.y - b.y);
    const cx = (a.x + b.x) / 2;
    const cy = (a.y + b.y) / 2;
    zoomAbout(cx, cy, Math.min(3, Math.max(0.4, (pinchScaleStart * dist) / pinchStart)));
    return;
  }

  if (draggingNode >= 0 && scene.value) {
    scene.value.pos[draggingNode * 2] += dx / view.value.scale;
    scene.value.pos[draggingNode * 2 + 1] += dy / view.value.scale;
    grid.value = new HitGrid(scene.value).build();
    markDirty();
    return;
  }

  view.value.tx += dx;
  view.value.ty += dy;
  markDirty();
}

function onPointerUp(e: PointerEvent): void {
  pointers.delete(e.pointerId);
  if (pointers.size < 2) pinchStart = 0;
  if (moved < 6) handleClick(e);
  draggingNode = -1;
}

function handleClick(e: PointerEvent): void {
  const p = worldPoint(e.clientX, e.clientY);
  const s = scene.value;
  if (!s) return;
  if (s.groups) {
    const hit = s.groups.find((g) => Math.hypot(g.x - p.x, g.y - p.y) <= 40);
    if (hit) expandType(hit.type);
    return;
  }
  const i = grid.value?.query(p.x, p.y, NODE_RADIUS / s0() + 4) ?? -1;
  if (i >= 0) emit('select', s.keys[i]);
}

function s0(): number {
  return view.value.scale;
}

function onWheel(e: WheelEvent): void {
  if (!props.interactive) return;
  e.preventDefault();
  zoomAbout(e.clientX, e.clientY, Math.min(3, Math.max(0.4, view.value.scale * (e.deltaY < 0 ? 1.1 : 0.9))));
}

function zoomAbout(clientX: number, clientY: number, next: number): void {
  const rect = host.value!.getBoundingClientRect();
  const ox = clientX - rect.left;
  const oy = clientY - rect.top;
  const before = view.value.scale;
  view.value.tx = ox - ((ox - view.value.tx) * next) / before;
  view.value.ty = oy - ((oy - view.value.ty) * next) / before;
  view.value.scale = next;
  markDirty();
}

function fit(): void {
  const s = scene.value;
  const el = host.value;
  if (!s || !el) return;
  const w = el.clientWidth;
  const h = el.clientHeight;
  const scale = Math.min(3, Math.max(0.4, Math.min(w / s.width, h / s.height)));
  view.value = { scale, tx: (w - s.width * scale) / 2, ty: 0 };
  markDirty();
}

function expandType(type: string): void {
  const next = new Set(expanded.value);
  next.add(type);
  expanded.value = next;
}

function expandAll(): void {
  expanded.value = new Set(scene.value?.types ?? []);
}

function collapseAll(): void {
  expanded.value = new Set();
}

/** Centre the viewport on a businessKey — the search-locate action from the parent page. */
function focusKey(key: string): void {
  const s = scene.value;
  const el = host.value;
  if (!s || !el) return;
  if (s.groups) expanded.value = new Set(s.types);
  const i = s.keys.indexOf(key);
  if (i < 0) return;
  const scale = Math.max(view.value.scale, 1);
  view.value = {
    scale,
    tx: el.clientWidth / 2 - s.pos[i * 2] * scale,
    ty: el.clientHeight / 2 - s.pos[i * 2 + 1] * scale,
  };
  emit('select', key);
  markDirty();
}

defineExpose({ focusKey, fit, expandAll, collapseAll, selectedFields, stats: () => ({ loose: scene.value?.loose.length ?? -1, groups: scene.value?.groups?.length ?? 0 }), drawCount: () => draws, viewState: () => ({ ...view.value }), pointerCount: () => pointers.size, canvasMode: () => useCanvas.value });

const collapsed = computed(() => !!scene.value?.groups);

// The SVG arm draws exactly what the scene declares drawable: super-nodes for folded types, real
// nodes for unfolded ones, and edges only between two unfolded nodes. Drawing every edge while the
// graph is folded put ~1,000 <line> elements on screen behind 9 circles.
const svgEdges = computed(() => {
  const sc = scene.value;
  if (!sc) return [];
  return sc.edges.filter((e) => (sc.flags[e.from] & NodeFlag.Grouped) === 0 && (sc.flags[e.to] & NodeFlag.Grouped) === 0);
});
const svgNodes = computed(() => scene.value?.loose ?? []);
const counts = computed(() => ({ nodes: props.nodes.length, edges: props.edges.length }));
</script>

<template>
  <div
    ref="host"
    class="fui-graph"
    :style="{ height: height + 'px' }"
    @wheel="onWheel"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <div class="fui-graph__bar">
      <FuiButton size="mini" :label="t('graph.fit')" @click="fit" />
      <FuiButton v-if="collapsed" size="mini" :label="t('graph.expandAll')" @click="expandAll" />
      <FuiButton v-else-if="expanded.size" size="mini" :label="t('graph.collapseAll')" @click="collapseAll" />
      <span class="fui-graph__count">{{ counts.nodes }} · {{ counts.edges }}</span>
    </div>

    <canvas v-if="useCanvas" ref="canvas" class="fui-graph__canvas" :aria-label="t('graph.canvasAria')" role="img" />
    <svg v-else class="fui-graph__svg" :viewBox="`0 0 ${scene?.width ?? 100} ${scene?.height ?? 100}`" :aria-label="t('graph.canvasAria')" role="img">
      <g :transform="`translate(${view.tx} ${view.ty}) scale(${view.scale})`">
        <line
          v-for="(e, i) in svgEdges"
          :key="'e' + i"
          :x1="scene!.pos[e.from * 2]"
          :y1="scene!.pos[e.from * 2 + 1]"
          :x2="scene!.pos[e.to * 2]"
          :y2="scene!.pos[e.to * 2 + 1]"
          class="fui-graph__edge"
        />
        <g v-for="g in scene?.groups ?? []" :key="'g' + g.type" class="fui-graph__node" @click.stop="expandType(g.type)">
          <circle :cx="g.x" :cy="g.y" r="26" />
          <text :x="g.x" :y="g.y" text-anchor="middle">{{ g.count }}</text>
        </g>
        <g
          v-for="i in svgNodes"
          :key="scene!.keys[i]"
          class="fui-graph__node"
          :class="{ 'is-sel': i === selected, 'is-orphan': (scene!.flags[i] & 1) !== 0, 'is-no-uid': (scene!.flags[i] & 2) !== 0, 'is-unresolved': (scene!.flags[i] & 32) !== 0 }"
          @click.stop="emit('select', scene!.keys[i])"
        >
          <circle :cx="scene!.pos[i * 2]" :cy="scene!.pos[i * 2 + 1]" r="10" />
          <text :x="scene!.pos[i * 2] + 14" :y="scene!.pos[i * 2 + 1] + 4">{{ scene!.keys[i] }}</text>
        </g>
      </g>
    </svg>

    <p v-if="selectedFields.length" class="fui-graph__fields" data-testid="graph-fields">
      {{ t('graph.refFields') }}: <code v-for="f in selectedFields" :key="f">{{ f }}</code>
    </p>
  </div>
</template>

<style scoped>
.fui-graph { position: relative; overflow: hidden; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); background: var(--fui-surface); touch-action: none; cursor: grab; }
.fui-graph__bar { position: absolute; top: 8px; right: 8px; z-index: 2; display: flex; gap: 6px; align-items: center; }
.fui-graph__count { font-size: 10px; color: var(--fui-text-tertiary); background: var(--fui-surface); padding: 2px 6px; border-radius: 6px; }
.fui-graph__canvas { width: 100%; height: 100%; display: block; }
.fui-graph__svg { width: 100%; height: 100%; display: block; }
.fui-graph__edge { stroke: var(--fui-muted); stroke-width: 1; }
.fui-graph__node circle { fill: var(--fui-primary); stroke: var(--fui-on-primary); stroke-width: 1.5; }
.fui-graph__node text { font-size: 12px; fill: var(--fui-text-secondary); }
.fui-graph__node.is-orphan circle { fill: var(--fui-warning); }
.fui-graph__node.is-no-uid circle { fill: var(--fui-danger); }
/* a dangling reference is ringed rather than recoloured: it is a statement about a field, and the
   fill already says whether the object has a UID */
.fui-graph__node.is-unresolved circle { stroke: var(--fui-danger); stroke-width: 3; }
.fui-graph__node.is-sel circle { stroke: var(--fui-text); stroke-width: 2.5; }
.fui-graph__node { cursor: pointer; }
.fui-graph__fields { position: absolute; left: 8px; bottom: 8px; margin: 0; font-size: 11px; background: var(--fui-surface); padding: 2px 6px; border-radius: 6px; }
.fui-graph__fields code { margin-right: 4px; background: var(--fui-surface-2); padding: 0 4px; }
</style>
