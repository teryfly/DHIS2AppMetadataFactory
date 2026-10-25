<script setup lang="ts">
import { computed, shallowRef, ref } from 'vue';
import type { SchemaNode, SchemaResolver } from '@/lib/openapi/resolve';
import { t as translate } from '@/lib/labels';

/**
 * One node of the OpenAPI schema tree (UM-CAP-003: 展开任意层级的嵌套 Schema 并解析 `$ref`).
 *
 * Disclosure is a real `<button>` that carries `aria-expanded` and an accessible name, and the
 * children live in `role="group"` — so the tree is operable from the keyboard alone and readable by
 * an AT, without a `<details>` element whose implicit control the FUI tokens would have to fight.
 *
 * Children are materialised on first open, through the resolver's budget: that is what keeps a
 * 1,000-operation document affordable, and it is why a cap or a cycle can be *reported* rather than
 * silently omitted. This component never renders a truncated list without a marker.
 *
 * It is recursive, so it refers to itself by its file name — the only self-reference in `src/fui`.
 */
const props = withDefaults(defineProps<{ node: SchemaNode; resolver: SchemaResolver; level?: number; defaultOpen?: boolean }>(), { level: 0, defaultOpen: false });

const open = ref(false);
const kids = shallowRef<SchemaNode[] | null>(null);

/** Materialise this node's children once. Deeper levels wait for their own click. */
function ensure(): void {
  if (kids.value === null && props.node.expandable) kids.value = props.resolver.childrenOf(props.node);
}

// The root of a tree opens on mount so a reader sees the first level of the body schema instead of
// one row and a puzzle; everything below stays lazy (UM-TECH-002 in the small).
if (props.defaultOpen) {
  ensure();
  open.value = props.node.expandable;
}

const expandable = computed(() => props.node.expandable);
const typeText = computed(() => props.node.types.join(' | '));
const markerText = computed(() => (props.node.marker ? translate(`api.doc.marker.${props.node.marker}`) : ''));
const enumText = computed(() => (props.node.enumValues.length ? props.node.enumValues.map(short).join(' · ') : ''));
const defaultText = computed(() => (props.node.hasDefault ? short(props.node.defaultValue) : ''));
const exampleText = computed(() => (props.node.hasExample ? short(props.node.exampleValue) : ''));
const toggleName = computed(() => `${open.value ? translate('api.doc.collapseNode') : translate('api.doc.expandNode')} ${props.node.name}`);

function short(value: unknown): string {
  if (value === null) return 'null';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function toggle(): void {
  if (!expandable.value) return;
  ensure();
  open.value = !open.value;
}

/** The tree can be driven from outside (e.g. "expand this branch") without duplicating the state. */
defineExpose({ toggle, open });
</script>

<template>
  <li class="oast" role="treeitem" :aria-level="level + 1" :aria-expanded="expandable ? open : undefined">
    <div class="oast__row">
      <button v-if="expandable" type="button" class="oast__twisty" :aria-label="toggleName" :aria-expanded="open" @click="toggle">
        <span aria-hidden="true">{{ open ? '−' : '+' }}</span>
      </button>
      <span v-else class="oast__twisty oast__twisty--leaf" aria-hidden="true">·</span>

      <span class="oast__name">{{ node.name }}</span>
      <span v-if="typeText" class="oast__type">{{ typeText }}</span>
      <span v-if="node.format" class="oast__format">{{ node.format }}</span>
      <span v-if="node.required" class="oast__req">{{ translate('api.doc.required') }}</span>
      <span v-if="node.ref" class="oast__ref" :title="node.ref">{{ node.ref }}</span>
      <span v-if="node.composeRefs.length" class="oast__compose">allOf {{ node.composeRefs.join(' + ') }}</span>
      <span v-if="markerText" class="oast__marker">{{ markerText }}</span>
    </div>

    <p v-if="node.description" class="oast__desc">{{ node.description }}</p>
    <ul v-if="enumText || defaultText || exampleText || node.constraints.length" class="oast__facts">
      <li v-if="enumText"><span>{{ translate('api.doc.enum') }}</span>{{ enumText }}</li>
      <li v-if="defaultText"><span>{{ translate('api.doc.default') }}</span>{{ defaultText }}</li>
      <li v-if="exampleText"><span>{{ translate('api.doc.declaredExampleValue') }}</span>{{ exampleText }}</li>
      <li v-for="chip in node.constraints" :key="chip"><span>{{ translate('api.doc.constraint') }}</span>{{ chip }}</li>
    </ul>

    <ul v-if="open && kids" class="oast__kids" role="group">
      <OpenApiSchemaTree v-for="(child, index) in kids" :key="`${child.pointer || node.name}-${child.name}-${index}`" :node="child" :resolver="resolver" :level="level + 1" />
    </ul>
  </li>
</template>

<style scoped>
.oast { list-style: none; margin: 0; padding: 0; }
.oast__row { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; min-height: 28px; }
.oast__twisty { flex: none; width: 20px; height: 20px; margin-right: 2px; border: 1px solid var(--fui-border); border-radius: 5px; background: var(--fui-surface); color: var(--fui-text-secondary); font-size: 12px; line-height: 1; cursor: pointer; }
.oast__twisty--leaf { border: 0; background: none; cursor: default; }
.oast__name { font-family: ui-monospace, monospace; font-size: var(--fui-fs-sm); }
.oast__type { color: var(--fui-primary); font-size: var(--fui-fs-xs); }
.oast__format { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); }
.oast__req { color: var(--fui-danger); font-size: 10px; border: 1px solid currentColor; border-radius: 4px; padding: 0 4px; }
.oast__ref { color: var(--fui-info); font-size: var(--fui-fs-xs); font-family: ui-monospace, monospace; }
.oast__compose { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); }
.oast__marker { color: var(--fui-warning); font-size: var(--fui-fs-xs); background: var(--fui-warning-bg); border-radius: 4px; padding: 0 4px; }
.oast__desc { margin: 0 0 0 26px; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.oast__facts { margin: 0 0 0 26px; padding: 0; list-style: none; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.oast__facts li span { color: var(--fui-muted); margin-right: 4px; }
.oast__kids { margin: 2px 0 0 26px; padding: 0 0 0 8px; list-style: none; border-left: 1px solid var(--fui-border); }
</style>
