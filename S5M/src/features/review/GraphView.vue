<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiFilterBar, FuiAsync, FuiGraphViewer, FuiEmpty, FuiSearchField, FuiVirtualList } from '@/fui';
import { useWorkflowOfProject } from '@/api/queries';
import { useObjectGraphForWorkflow } from '@/api/objectGraph';
import { readIntentOf, type ReadIntent } from '@/api/notGenerated';
import type { GraphEdgeView, GraphNodeView } from '@/api/types';
import { dispositionLabel } from '@/lib/labels';

// UM-REV-002 / UM-CAP-002. The route stays project-scoped (a reader arrives from a project), while
// the graph itself is read by workflow: the build it came from belongs to a workflow, not to a
// version string the browser picked (CP-UI-020).
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));
const workflow = useWorkflowOfProject(projectId);
const workflowId = computed(() => String(workflow.data.value?.id ?? ''));

// S1 narrows by DHIS2 type and business key; the type choices come from the first unfiltered read,
// because after a filter the answer only contains what survived it.
const allTypes = ref<string[]>([]);
const typeFilter = ref('');
const search = ref('');

const graph = useObjectGraphForWorkflow(workflowId, () => ({
  dhis2Type: typeFilter.value ? [typeFilter.value] : undefined,
}));

const data = computed(() => graph.data.value);
const nodes = computed<GraphNodeView[]>(() => (data.value?.nodes ?? []) as GraphNodeView[]);
const edges = computed<GraphEdgeView[]>(() => (data.value?.edges ?? []) as GraphEdgeView[]);
const orphans = computed<string[]>(() => data.value?.orphans ?? []);
const unresolved = computed(() => data.value?.unresolvedRegistryRefs ?? []);
const unresolvedKeys = computed(() => [...new Set(unresolved.value.map((r) => String(r.fromBusinessKey)))]);
const orphanSet = computed(() => new Set(orphans.value));
const types = computed(() => new Set(nodes.value.map((n) => String(n.dhis2Type ?? ''))).size);

watch(data, (view) => {
  if (view && !typeFilter.value && allTypes.value.length === 0) {
    allTypes.value = [...new Set((view.nodes ?? []).map((n) => String(n.dhis2Type ?? '')).filter(Boolean))].sort();
  }
});

const typeOptions = computed(() => [
  { value: '', label: t('graph.allTypes') },
  ...allTypes.value.map((k) => ({ value: k, label: k })),
]);

// S1 takes no free-text query: the search locates inside what this read returned, which is why the
// type filter is the one that goes over the wire on a large model.
const hits = computed<string[]>(() => {
  const term = search.value.trim().toLowerCase();
  if (!term) return [];
  return nodes.value
    .filter((n) => [n.businessKey, n.dhis2Type, n.uid, n.disposition]
      .some((v) => String(v ?? '').toLowerCase().includes(term)))
    .map((n) => String(n.businessKey));
});

// UM-AC-19: a graph that has not been compiled yet is guidance, not an empty panel.
const intent = computed<ReadIntent>(() => (graph.isError.value ? readIntentOf(graph.error.value, 'graph') : { kind: 'ok' }));

const selected = ref('');
const viewer = ref<InstanceType<typeof FuiGraphViewer> | null>(null);
const sel = computed(() => nodes.value.find((n) => n.businessKey === selected.value));
const outgoing = computed(() => edges.value.filter((e) => e.fromBusinessKey === selected.value));
const incoming = computed(() => edges.value.filter((e) => e.toBusinessKey === selected.value));
const selRefs = computed(() => unresolved.value.filter((r) => String(r.fromBusinessKey) === selected.value));

// UM-NAV-004: a search hit on a businessKey arrives here as `?businessKey=`, and the jump has to
// select that node. Landing on a canvas the operator then searches by hand is not a jump.
const wantedKey = computed(() => String(route.query.businessKey ?? ''));
watch(
  [nodes, wantedKey],
  ([list, key]) => {
    if (key && !selected.value && list.some((n) => String(n.businessKey) === key)) selected.value = key;
  },
  { immediate: true },
);

/** Search locates rather than re-rendering: the reader steps through the hits, centred each time. */
const hitCursor = ref(0);
function locate(): void {
  if (!hits.value.length) return;
  hitCursor.value = (hitCursor.value + 1) % hits.value.length;
  viewer.value?.focusKey(hits.value[hitCursor.value]);
}

function toneForDisposition(d?: string | null): 'success' | 'info' | 'danger' | 'muted' | 'primary' {
  switch (d) {
    case 'CREATE': return 'success';
    case 'UPDATE': return 'info';
    case 'DEACTIVATE': return 'danger';
    case 'EXTERNAL': case 'REFERENCE': return 'muted';
    default: return 'primary';
  }
}

const buildKind = computed(() => {
  const kind = String(data.value?.kind ?? '');
  if (kind === 'BASELINE') return t('graph.kindBaseline');
  if (kind === 'ENVIRONMENT') return t('graph.kindEnvironment');
  return kind || t('graph.kindUnknown');
});
</script>

<template>
  <div class="gph">
    <p class="gph__build">
      {{ buildKind }}<template v-if="data?.blueprintVersion"> · {{ data.blueprintVersion }}</template><template v-if="data?.environmentKey"> · {{ data.environmentKey }}</template><template v-if="data?.runId"> · {{ t('graph.buildRun') }} {{ data.runId }}</template>
    </p>
    <FuiFilterBar v-if="allTypes.length > 1" v-model="typeFilter" :options="typeOptions" />
    <div class="gph__search">
      <FuiSearchField v-model="search" :placeholder="t('graph.search')" shape="round" @keyup.enter="locate" />
      <FuiStatusTag v-if="hits.length" tone="info" :label="`${hits.length}`" />
    </div>

    <div v-if="nodes.length" class="gph__bar">
      <span>{{ t('graph.nodes') }} {{ nodes.length }}</span>
      <span>{{ t('graph.edges') }} {{ edges.length }}</span>
      <span>{{ t('graph.types') }} {{ types }}</span>
      <span>{{ t('graph.orphans') }} {{ orphans.length }}</span>
      <span v-if="unresolved.length" class="gph__unresolved">{{ t('graph.unresolved') }} {{ unresolved.length }}</span>
    </div>

    <FuiAsync :loading="graph.isFetching.value || workflow.isFetching.value" :intent="intent" :project-id="projectId" :empty="!nodes.length && !graph.isFetching.value" :empty-title="t('graph.title')" :empty-hint="t('graph.noGraph')">
      <div class="gph__grid">
        <!-- list: virtualised, because 5,000 rows must not mean 5,000 DOM nodes (UM-REV-006) -->
        <div class="gph__list">
          <FuiVirtualList :items="nodes" :row-height="52" :height="360">
            <template #default="{ item }">
              <button
                class="gph__item"
                :class="{ 'is-sel': (item as GraphNodeView).businessKey === selected }"
                @click="selected = String((item as GraphNodeView).businessKey)"
              >
                <FuiStatusTag :tone="toneForDisposition((item as GraphNodeView).disposition)" :label="dispositionLabel((item as GraphNodeView).disposition)" />
                <span class="gph__item-key">{{ (item as GraphNodeView).businessKey }}</span>
                <span class="gph__item-type">{{ (item as GraphNodeView).dhis2Type }}</span>
                <span v-if="!(item as GraphNodeView).uid" class="gph__warn">⚠ {{ t('graph.noUid') }}</span>
              </button>
            </template>
          </FuiVirtualList>
        </div>

        <!-- graph: rendered on mobile too — UM-AC-18 measures pan and zoom on a mid-range phone -->
        <div class="gph__canvas">
          <FuiGraphViewer
            ref="viewer"
            :nodes="nodes"
            :edges="edges"
            :selected-key="selected"
            :matches="hits"
            :orphans="orphans"
            :unresolved="unresolvedKeys"
            @select="(k) => (selected = k)"
          />
        </div>

        <!-- detail -->
        <div class="gph__detail">
          <FuiCard v-if="sel" :title="t('graph.selected')">
            <div class="gph__row"><span>{{ t('graph.selected') }}</span><b>{{ sel.businessKey }}</b></div>
            <div class="gph__row"><span>UID</span>{{ sel.uid ?? '—' }}</div>
            <div class="gph__row"><span>{{ t('environments.class') }}</span>{{ sel.dhis2Type }}</div>
            <div class="gph__row"><span>{{ t('graph.degree') }}</span>{{ sel.inDegree }} / {{ sel.outDegree }}</div>
            <div class="gph__chips">
              <FuiStatusTag v-if="sel.managed" tone="info" :label="t('graph.managed')" />
              <FuiStatusTag v-if="sel.inPayload" tone="success" :label="t('graph.inPayload')" />
              <FuiStatusTag v-if="orphanSet.has(sel.businessKey)" tone="warning" :label="t('graph.orphans')" />
              <FuiStatusTag v-if="!sel.uid" tone="danger" :label="t('graph.noUid')" />
            </div>
            <!-- an annotation about this object, not a second issue list (BP-TD-009) -->
            <div v-if="selRefs.length" class="gph__refs">
              <div class="gph__rel-h">{{ t('graph.unresolved') }}</div>
              <div v-for="(r, i) in selRefs" :key="'r' + i" class="gph__rel-item">
                {{ r.field }} → <code>{{ r.target }}</code>
              </div>
              <button class="gph__jump" @click="router.push(`/projects/${projectId}/issues`)">{{ t('graph.seeIssues') }} →</button>
            </div>
            <div class="gph__rel">
              <div class="gph__rel-h">{{ t('graph.references') }} ({{ outgoing.length }})</div>
              <div v-for="(e, i) in outgoing" :key="'o' + i" class="gph__rel-item" @click="selected = e.toBusinessKey">→ {{ e.toBusinessKey }} <span class="gph__fld">{{ e.field }}</span></div>
              <div class="gph__rel-h">{{ t('graph.referencedBy') }} ({{ incoming.length }})</div>
              <div v-for="(e, i) in incoming" :key="'i' + i" class="gph__rel-item" @click="selected = e.fromBusinessKey">← {{ e.fromBusinessKey }} <span class="gph__fld">{{ e.field }}</span></div>
            </div>
            <button class="gph__jump" @click="router.push(`/projects/${projectId}/metadata`)">{{ t('graph.jumpRegistry') }} →</button>
          </FuiCard>
          <FuiEmpty v-else :title="t('graph.selected')" :hint="t('common.selectToContinue')" icon="point" />
        </div>
      </div>
    </FuiAsync>
  </div>
</template>

<style scoped>
.gph { padding: var(--fui-sp-3) var(--fui-sp-4); }
.gph__search { display: flex; align-items: center; gap: 8px; }
.gph__bar { display: flex; gap: 14px; flex-wrap: wrap; align-items: center; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); padding: 6px 0; }
.gph__unresolved { color: var(--fui-danger); }
.gph__build { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); font-family: var(--fui-mono, ui-monospace, monospace); }
.gph__refs { margin-top: var(--fui-sp-2); }
.gph__grid { display: grid; grid-template-columns: 1fr; gap: var(--fui-sp-3); }
@media (min-width: 1440px) {
  .gph__grid { grid-template-columns: minmax(220px, 1fr) minmax(320px, 1.4fr) minmax(240px, 1fr); }
}
.gph__list { min-width: 0; }
.gph__item { display: flex; gap: 6px; align-items: center; width: 100%; padding: 8px; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); text-align: left; min-height: 44px; font-size: var(--fui-fs-sm); }
.gph__item.is-sel { border-color: var(--fui-primary); background: var(--fui-info-bg); }
.gph__item-key { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gph__item-type { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); }
.gph__warn { color: var(--fui-danger); font-size: var(--fui-fs-xs); }
.gph__canvas { min-width: 0; }
.gph__row { display: flex; justify-content: space-between; font-size: var(--fui-fs-sm); padding: 4px 0; gap: 8px; }
.gph__row span { color: var(--fui-text-tertiary); }
.gph__chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.gph__rel { margin-top: var(--fui-sp-2); }
.gph__rel-h { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 8px 0 4px; }
.gph__rel-item { font-size: var(--fui-fs-sm); padding: 4px 0; cursor: pointer; }
.gph__fld { color: var(--fui-info); font-size: var(--fui-fs-xs); }
.gph__jump { width: 100%; margin-top: var(--fui-sp-3); min-height: var(--fui-tap-min); border: 0; background: none; color: var(--fui-primary); font-size: var(--fui-fs-sm); }
</style>
