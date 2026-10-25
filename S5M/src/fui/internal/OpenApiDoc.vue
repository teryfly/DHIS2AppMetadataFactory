<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import FuiSkeleton from '../FuiSkeleton.vue';
import FuiNotice from '../FuiNotice.vue';
import FuiStatusTag from '../FuiStatusTag.vue';
import FuiSearchField from '../FuiSearchField.vue';
import FuiFilterBar from '../FuiFilterBar.vue';
import FuiGuidedEmpty from '../FuiGuidedEmpty.vue';
import FuiVirtualList from '../FuiVirtualList.vue';
import OpenApiSchemaTree from './OpenApiSchemaTree.vue';
import { parseApiDocument } from '@/lib/openapi/parseDoc';
import type { DocWarning } from '@/lib/openapi/parseDoc';
import { buildDocModel } from '@/lib/openapi/docModel';
import type { ApiDocModel, ApiOperation, ApiParameter, ApiResponse } from '@/lib/openapi/docModel';
import { createResolver } from '@/lib/openapi/resolve';
import type { SchemaNode, SchemaResolver } from '@/lib/openapi/resolve';
import { exampleAsText, exampleFromSchema } from '@/lib/openapi/exampleFromSchema';
import { searchOperations } from '@/lib/openapi/search';
import { missingArtifactIntent, readIntentOf } from '@/api/notGenerated';
import type { ReadIntent } from '@/api/notGenerated';
import { t as translate } from '@/lib/labels';

/**
 * UM-CAP-003 OpenAPI document view (desktop, M3) — the heavy core behind `FuiOpenApiViewer`.
 *
 * Read-only by requirement (UM-REV-005, 总体 SRS C-08, S4 DX-BR-01): this view renders the artifact
 * the Factory compiled. It has no control that sends a request, no field for a base URL and no code
 * path that builds one; the only thing it can navigate to is another part of the same document. An
 * interface is debugged by a front-end developer outside the Factory, with the development package.
 *
 * Examples are *derived from the schema* and labelled as such. S2 keeps the real
 * `requestExample`/`responseExample` in `api/api-reference.md` and `api/examples/*`, which no
 * endpoint in the current S1 contract can serve, so anything this view could show for an example is
 * a synthesis — presenting it as the document's own example would be a fabrication.
 *
 * Error responses are shown as absent when the document declares none, which for every document S2
 * compiles today is always (`_render` writes only `responses["200"]`).
 */
// Type-based props with no `withDefaults`: `error` carries `unknown` (a failed read of whatever
// shape the transport produced), and the meaningful "nothing failed" value is the absence of one.
const props = defineProps<{ doc: string | null; loading?: boolean; error?: unknown; projectId?: string }>();

/** UM-TECH-002 keeps the cost where the interest is: parse and flatten only when text changes. */
const parsed = computed(() => parseApiDocument(props.doc));
const model = computed<ApiDocModel | null>(() => (parsed.value.ok ? buildDocModel(parsed.value) : null));
const failure = computed(() => (parsed.value.ok ? null : parsed.value));

const tag = ref('');
const method = ref('');
const query = ref('');
const term = ref('');

// 200 ms debounce, applied here (the caller) rather than inside the pure index, so `search.ts`
// stays synchronous and testable.
let timer: ReturnType<typeof setTimeout> | null = null;
watch(query, (value) => {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    term.value = value;
  }, 200);
});
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer);
});

const ALL = '';
const methodOptions = computed(() => {
  const seen = new Set<string>();
  for (const operation of model.value?.operations ?? []) seen.add(operation.method);
  return [{ value: ALL, label: translate('api.doc.allMethods') }, ...[...seen].sort().map((m) => ({ value: m, label: m }))];
});

const hits = computed(() => {
  const m = model.value;
  if (!m) return new Set<string>();
  return new Set(searchOperations(m.index, term.value));
});

const visible = computed<ApiOperation[]>(() => {
  const m = model.value;
  if (!m) return [];
  return m.operations.filter((o) => (tag.value === ALL || o.tags.includes(tag.value)) && (method.value === ALL || o.method === method.value) && hits.value.has(o.id));
});

const selectedId = ref('');
const selected = computed<ApiOperation | null>(() => {
  const list = visible.value;
  return list.find((o) => o.id === selectedId.value) ?? list[0] ?? null;
});

// A fresh resolver per selected operation: the 400-node cap then means "nodes expanded for what is
// on screen", and switching operations cannot exhaust a document-wide budget the reader never used.
interface ParameterView {
  parameter: ApiParameter;
  node: SchemaNode | null;
}

interface ResponseView {
  response: ApiResponse;
  node: SchemaNode | null;
  example: ExampleView | null;
}

interface ExampleView {
  text: string;
  /** False only when the document itself declared an example for this slot. */
  derived: boolean;
  stopped: string;
}

interface ExpansionView {
  resolver: SchemaResolver;
  bodyNode: SchemaNode | null;
  bodyExample: ExampleView | null;
  parameters: ParameterView[];
  responses: ResponseView[];
}

function exampleView(doc: ApiDocModel, schema: unknown, declared: boolean, declaredValue: unknown): ExampleView | null {
  if (declared) {
    let text = '';
    try {
      text = JSON.stringify(declaredValue, null, 2) ?? String(declaredValue);
    } catch {
      text = String(declaredValue);
    }
    return { text, derived: false, stopped: '' };
  }
  if (schema === null || schema === undefined) return null;
  const result = exampleFromSchema(doc.root, schema);
  return { text: exampleAsText(result), derived: true, stopped: result.stopped };
}

const expansion = ref<ExpansionView | null>(null);

/**
 * Built when the *identity* of the selected operation changes, not when the filtered list does: a
 * search keystroke must not collapse the branch a reader is halfway through reading.
 */
function buildExpansion(): void {
  const m = model.value;
  const operation = selected.value;
  if (!m || !operation) {
    expansion.value = null;
    return;
  }
  const resolver = createResolver(m.root);
  const body = operation.body;
  expansion.value = {
    resolver,
    bodyNode: body?.schema ? resolver.node('requestBody', body.schema, { role: 'body' }) : null,
    bodyExample: body ? exampleView(m, body.schema, body.hasDeclaredExample, body.declaredExample) : null,
    parameters: operation.parameters.map((parameter) => ({
      parameter,
      node: parameter.schema ? resolver.node(parameter.name, parameter.schema, { role: 'parameter' }) : null,
    })),
    responses: operation.responses.map((response) => ({
      response,
      node: response.hasSchema ? resolver.node(response.status, response.schema, { role: 'response' }) : null,
      example: exampleView(m, response.schema, response.hasDeclaredExample, response.declaredExample),
    })),
  };
}

watch(() => `${selected.value?.id ?? ''}|${model.value?.info.title ?? ''}`, buildExpansion, { immediate: true });

const intent = computed<ReadIntent | null>(() => {
  if (props.loading || model.value) return null;
  if (props.error) return readIntentOf(props.error, 'apiDoc');
  if (failure.value?.reason === 'empty') return missingArtifactIntent('apiDoc');
  return null;
});

const operationCount = computed(() => model.value?.counts.operations ?? 0);
const pathCount = computed(() => model.value?.counts.paths ?? 0);
const schemaCount = computed(() => model.value?.counts.schemas ?? 0);
const searchCount = computed(() => visible.value.length);
const docLabel = computed(() => `${model.value?.info.title || translate('api.doc.title')} · ${model.value?.info.version ?? ''}`);
const methodTone = (value: string): 'success' | 'info' | 'warning' | 'danger' | 'primary' =>
  value === 'GET' ? 'success' : value === 'POST' ? 'info' : value === 'PUT' || value === 'PATCH' ? 'warning' : 'danger';
const warningText = (warning: DocWarning): string => translate(`api.doc.warning.${warning}`);
</script>

<template>
  <FuiSkeleton v-if="loading" :rows="6" />

  <FuiGuidedEmpty v-else-if="intent" :intent="intent" :project-id="projectId ?? ''" />

  <div v-else-if="failure" class="oad oad--failed">
    <FuiNotice tone="danger" :text="translate(`api.doc.parse.${failure.reason}`)" />
    <p v-if="failure.message" class="oad__detail">{{ failure.message }}</p>
  </div>

  <div v-else-if="model" class="oad">
    <header class="oad__head">
      <div class="oad__title">
        <strong>{{ docLabel }}</strong>
        <FuiStatusTag tone="info" :label="`OpenAPI ${model.specVersion || '?'}`" />
        <FuiStatusTag tone="muted" :label="model.format.toUpperCase()" />
        <FuiStatusTag tone="primary" :label="`${translate('api.doc.operationCount')} ${operationCount}`" />
        <FuiStatusTag tone="muted" :label="`${translate('api.doc.pathCount')} ${pathCount}`" />
        <FuiStatusTag tone="muted" :label="`${translate('api.doc.schemaCount')} ${schemaCount}`" />
      </div>
      <p v-if="model.info.description" class="oad__desc">{{ model.info.description }}</p>
      <div v-if="model.info.extra.length || model.servers.length" class="oad__chips">
        <FuiStatusTag v-for="extra in model.info.extra" :key="extra" tone="muted" :label="extra" />
        <FuiStatusTag v-for="server in model.servers" :key="server.url" tone="muted" :label="server.url" />
      </div>
      <FuiNotice tone="info" :text="translate('api.doc.readOnly')" />
      <p v-if="model.warnings.length" class="oad__warnings">
        <span v-for="warning in model.warnings" :key="warning" class="oad__warning">{{ warningText(warning) }}</span>
      </p>
      <div v-if="model.securitySchemes.length" class="oad__security">
        <span class="oad__security-label">{{ translate('api.doc.security') }}</span>
        <FuiStatusTag v-for="scheme in model.securitySchemes" :key="scheme.id" tone="warning" :label="`${scheme.id} · ${scheme.type}${scheme.detail ? ` · ${scheme.detail}` : ''}`" />
      </div>
    </header>

    <div class="oad__body">
      <nav class="oad__nav" :aria-label="translate('api.doc.browse')">
        <FuiSearchField v-model="query" :placeholder="translate('api.doc.search')" data-testid="oad-search" />
        <FuiFilterBar v-model="method" :options="methodOptions" />

        <div class="oad__tags" role="listbox" :aria-label="translate('api.doc.tags')">
          <button type="button" class="oad__tag" :class="{ 'is-active': tag === ALL }" role="option" :aria-selected="tag === ALL" @click="tag = ALL">
            {{ translate('api.doc.allTags') }}
            <span class="oad__count">{{ operationCount }}</span>
          </button>
          <button
            v-for="entry in model.tags"
            :key="entry.name"
            type="button"
            class="oad__tag"
            :class="{ 'is-active': tag === entry.name }"
            role="option"
            :aria-selected="tag === entry.name"
            :aria-label="entry.name"
            @click="tag = entry.name"
          >
            {{ entry.name }}
            <span class="oad__count">{{ entry.operations.length }}</span>
          </button>
        </div>

        <p class="oad__hits">{{ translate('api.doc.matched', { n: searchCount }) }}</p>

        <FuiVirtualList :items="visible" :row-height="58" :height="420">
          <template #default="{ item }">
            <button
              type="button"
              class="oad__op"
              :class="{ 'is-active': (item as ApiOperation).id === selected?.id }"
              :aria-current="(item as ApiOperation).id === selected?.id ? 'true' : undefined"
              :aria-label="`${(item as ApiOperation).method} ${(item as ApiOperation).id}`"
              @click="selectedId = String((item as ApiOperation).id)"
            >
              <span class="oad__verb">{{ (item as ApiOperation).method }}</span>
              <span class="oad__op-main">
                <span class="oad__op-id">{{ (item as ApiOperation).id }}</span>
                <span class="oad__op-path">{{ (item as ApiOperation).path }}</span>
              </span>
              <span v-if="(item as ApiOperation).deprecated" class="oad__dep">{{ translate('api.doc.deprecated') }}</span>
            </button>
          </template>
        </FuiVirtualList>

        <p v-if="!visible.length" class="oad__empty">{{ translate('api.doc.noMatch') }}</p>
      </nav>

      <main class="oad__main" :aria-label="translate('api.doc.detail')">
        <p v-if="!selected" class="oad__empty">{{ translate('api.doc.selectOperation') }}</p>

        <template v-else-if="expansion">
          <div class="oad__op-head">
            <FuiStatusTag :tone="methodTone(selected.method)" :label="selected.method" />
            <code class="oad__path">{{ selected.path }}</code>
            <strong class="oad__id">{{ selected.id }}</strong>
            <FuiStatusTag v-for="entry in selected.tags" :key="entry" tone="muted" :label="entry" />
            <FuiStatusTag v-if="selected.deprecated" tone="danger" :label="translate('api.doc.deprecated')" />
          </div>
          <p v-if="selected.summary" class="oad__sum">{{ selected.summary }}</p>
          <p v-if="selected.description" class="oad__desc">{{ selected.description }}</p>

          <section class="oad__section">
            <h3 class="oad__h">{{ translate('api.doc.registryRefs') }}</h3>
            <div v-if="selected.registryRefs.length" class="oad__chips">
              <FuiStatusTag v-for="registryRef in selected.registryRefs" :key="registryRef" tone="info" :label="registryRef" />
            </div>
            <p v-else class="oad__none">{{ translate('api.doc.noRegistryRefs') }}</p>
            <div v-if="selected.intentNotes.length" class="oad__chips">
              <span class="oad__notes-label">{{ translate('api.doc.intentNotes') }}</span>
              <FuiStatusTag v-for="note in selected.intentNotes" :key="note" tone="muted" :label="note" />
            </div>
          </section>

          <section class="oad__section">
            <h3 class="oad__h">{{ translate('api.doc.parameters') }}</h3>
            <table v-if="selected.parameters.length" class="oad__table">
              <thead>
                <tr>
                  <th scope="col">{{ translate('api.doc.colName') }}</th>
                  <th scope="col">{{ translate('api.doc.colIn') }}</th>
                  <th scope="col">{{ translate('api.doc.colType') }}</th>
                  <th scope="col">{{ translate('api.doc.colRequired') }}</th>
                  <th scope="col">{{ translate('api.doc.colDescription') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in expansion.parameters" :key="`${row.parameter.location}-${row.parameter.name}`">
                  <td><code>{{ row.parameter.name }}</code></td>
                  <td>{{ row.parameter.location }}</td>
                  <td>
                    <span class="oad__type">{{ row.node?.types.join(' | ') || '—' }}</span>
                    <span v-if="row.parameter.ref" class="oad__ref">{{ row.parameter.ref }}</span>
                  </td>
                  <td>{{ row.parameter.required ? translate('api.doc.required') : '' }}</td>
                  <td>{{ row.parameter.description }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="oad__none">{{ translate('api.doc.noParameters') }}</p>
          </section>

          <section class="oad__section">
            <h3 class="oad__h">{{ translate('api.doc.requestBody') }}</h3>
            <template v-if="selected.body">
              <p class="oad__meta">
                <span>{{ translate('api.doc.contentType') }}</span>
                <code>{{ selected.body.contentType || 'application/json' }}</code>
                <span v-if="selected.body.required" class="oad__req">{{ translate('api.doc.required') }}</span>
                <span v-if="selected.body.ref" class="oad__ref">{{ selected.body.ref }}</span>
              </p>
              <ul v-if="expansion.bodyNode" class="oast-root" role="tree" :aria-label="translate('api.doc.requestBody')">
                <OpenApiSchemaTree :node="expansion.bodyNode" :resolver="expansion.resolver" default-open />
              </ul>
              <div v-if="expansion.bodyExample" class="oad__example">
                <p class="oad__example-note" :class="{ 'is-declared': !expansion.bodyExample.derived }">
                  {{ translate(expansion.bodyExample.derived ? 'api.doc.derivedExample' : 'api.doc.declaredExample') }}
                </p>
                <pre class="oad__pre">{{ expansion.bodyExample.text }}</pre>
                <p v-if="expansion.bodyExample.stopped" class="oad__stopped">{{ translate('api.doc.exampleStopped') }}</p>
              </div>
            </template>
            <p v-else class="oad__none">{{ translate('api.doc.noBody') }}</p>
          </section>

          <section class="oad__section">
            <h3 class="oad__h">{{ translate('api.doc.responses') }}</h3>
            <p v-if="!selected.responses.length" class="oad__none">{{ translate('api.doc.noResponses') }}</p>
            <div v-for="(row, rowIndex) in expansion.responses" :key="`${selected.id}-${row.response.status}`" class="oad__response">
              <div class="oad__response-head">
                <FuiStatusTag :tone="row.response.isError ? 'danger' : 'success'" :label="row.response.isDefault ? translate('api.doc.defaultResponse') : row.response.status" />
                <span v-if="row.response.description" class="oad__response-desc">{{ row.response.description }}</span>
                <span v-if="row.response.ref" class="oad__ref">{{ row.response.ref }}</span>
                <span v-if="row.response.headers.length" class="oad__meta">headers: {{ row.response.headers.join(', ') }}</span>
              </div>
              <ul v-if="row.node" class="oast-root" role="tree" :aria-label="`${translate('api.doc.responseSchema')} ${row.response.status}`">
                <OpenApiSchemaTree :node="row.node" :resolver="expansion.resolver" :default-open="rowIndex === 0" />
              </ul>
              <p v-else-if="!row.response.hasSchema" class="oad__none">{{ translate('api.doc.noSchema') }}</p>
              <div v-if="row.example" class="oad__example">
                <p class="oad__example-note" :class="{ 'is-declared': !row.example.derived }">{{ translate(row.example.derived ? 'api.doc.derivedExample' : 'api.doc.declaredExample') }}</p>
                <pre class="oad__pre">{{ row.example.text }}</pre>
                <p v-if="row.example.stopped" class="oad__stopped">{{ translate('api.doc.exampleStopped') }}</p>
              </div>
            </div>

            <h4 class="oad__h oad__h--sub">{{ translate('api.doc.errorResponses') }}</h4>
            <p v-if="!selected.errorResponses.length" class="oad__none">{{ translate('api.doc.noErrorResponses') }}</p>
            <ul v-else class="oad__errors">
              <li v-for="errorResponse in selected.errorResponses" :key="`e-${errorResponse.status}`">
                <FuiStatusTag tone="danger" :label="errorResponse.isDefault ? translate('api.doc.defaultResponse') : errorResponse.status" />
                <span>{{ errorResponse.description || translate('api.doc.noDescription') }}</span>
              </li>
            </ul>
          </section>

          <section v-if="selected.security.length" class="oad__section">
            <h3 class="oad__h">{{ translate('api.doc.operationSecurity') }}</h3>
            <div class="oad__chips">
              <FuiStatusTag v-for="scheme in selected.security" :key="scheme" tone="warning" :label="scheme" />
            </div>
          </section>
        </template>
      </main>
    </div>
  </div>
</template>

<style scoped>
.oad { display: flex; flex-direction: column; gap: var(--fui-sp-3); }
.oad--failed { padding: var(--fui-sp-4); }
.oad__head { display: flex; flex-direction: column; gap: 6px; }
.oad__title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.oad__desc, .oad__sum { margin: 0; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.oad__detail { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); font-family: ui-monospace, monospace; }
.oad__chips { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.oad__warnings { margin: 0; display: flex; gap: 8px; flex-wrap: wrap; }
.oad__warning { font-size: var(--fui-fs-xs); color: var(--fui-warning); background: var(--fui-warning-bg); border-radius: 4px; padding: 2px 6px; }
.oad__security { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: var(--fui-fs-xs); }
.oad__security-label { color: var(--fui-text-tertiary); }
.oad__body { display: grid; grid-template-columns: minmax(280px, 360px) minmax(0, 1fr); gap: var(--fui-sp-4); align-items: start; }
.oad__nav { display: flex; flex-direction: column; gap: 6px; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); padding: var(--fui-sp-2); background: var(--fui-surface); }
.oad__tags { display: flex; flex-direction: column; max-height: 150px; overflow: auto; gap: 2px; }
.oad__tag { display: flex; justify-content: space-between; gap: 8px; min-height: 30px; padding: 4px 8px; border: 0; border-radius: var(--fui-radius-btn); background: none; color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); text-align: left; cursor: pointer; }
.oad__tag.is-active { background: var(--fui-info-bg); color: var(--fui-primary); font-weight: 600; }
.oad__count { color: var(--fui-text-tertiary); font-size: var(--fui-fs-xs); }
.oad__hits { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.oad__op { display: flex; align-items: center; gap: 8px; width: 100%; min-height: 50px; padding: 6px 8px; border: 1px solid transparent; border-radius: var(--fui-radius-btn); background: var(--fui-surface); color: var(--fui-text); font-size: var(--fui-fs-sm); text-align: left; cursor: pointer; }
.oad__op.is-active { border-color: var(--fui-primary); background: var(--fui-info-bg); }
.oad__verb { flex: none; width: 52px; font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-primary); }
.oad__op-main { display: flex; flex-direction: column; min-width: 0; }
.oad__op-id { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.oad__op-path { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.oad__dep { flex: none; font-size: 10px; color: var(--fui-danger); }
.oad__main { min-width: 0; display: flex; flex-direction: column; gap: var(--fui-sp-2); }
.oad__empty { font-size: var(--fui-fs-sm); color: var(--fui-text-tertiary); }
.oad__op-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.oad__path { font-size: var(--fui-fs-sm); background: var(--fui-surface-2); padding: 2px 6px; border-radius: 6px; }
.oad__id { font-size: var(--fui-fs-sm); }
.oad__section { border-top: 1px solid var(--fui-border); padding-top: var(--fui-sp-2); }
.oad__h { margin: 0 0 6px; font-size: var(--fui-fs-sm); color: var(--fui-text); }
.oad__h--sub { margin-top: var(--fui-sp-3); }
.oad__none { margin: 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.oad__meta { margin: 0 0 6px; display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.oad__type { color: var(--fui-primary); font-size: var(--fui-fs-xs); }
.oad__ref { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-info); }
.oad__req { color: var(--fui-danger); font-size: 10px; border: 1px solid currentColor; border-radius: 4px; padding: 0 4px; }
.oad__table { width: 100%; border-collapse: collapse; font-size: var(--fui-fs-xs); }
.oad__table th, .oad__table td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--fui-border); vertical-align: top; }
.oad__table th { color: var(--fui-text-tertiary); font-weight: 500; }
.oast-root { margin: 0; padding: 0; list-style: none; }
.oad__response { padding: var(--fui-sp-2) 0; border-top: 1px dashed var(--fui-border); }
.oad__response-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.oad__response-desc { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.oad__errors { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; font-size: var(--fui-fs-xs); }
.oad__example { margin-top: 6px; }
.oad__example-note { margin: 0 0 4px; font-size: var(--fui-fs-xs); color: var(--fui-warning); }
.oad__example-note.is-declared { color: var(--fui-success); }
.oad__pre { margin: 0; max-height: 260px; overflow: auto; background: var(--fui-surface-2); border-radius: var(--fui-radius-btn); padding: 8px; font-size: 11px; line-height: 1.5; }
.oad__stopped { margin: 4px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-warning); }
</style>
