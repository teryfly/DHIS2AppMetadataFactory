<script setup lang="ts">
import { computed } from 'vue';
import FuiSchemaField from './FuiSchemaField.vue';
import type { JsonSchema } from './schemaTypes';

const props = defineProps<{ schema?: JsonSchema; modelValue: Record<string, unknown> }>();
const emit = defineEmits<{ (e: 'update:modelValue', v: Record<string, unknown>): void }>();

const root = computed<JsonSchema>(() => {
  const s = props.schema;
  if (!s) return { type: 'object', properties: {} };
  // Some S2 schemas wrap fields under definitions.root.
  if (!s.properties && s.definitions && (s.definitions as Record<string, JsonSchema>)['root']?.properties) {
    return (s.definitions as Record<string, JsonSchema>)['root'] as JsonSchema;
  }
  return s;
});

/** The whole section document. Children resolve their `$ref` against this, not against `root`. */
const document = computed<JsonSchema>(() => props.schema ?? root.value);

const properties = computed<Record<string, JsonSchema>>(() => (root.value.properties ?? {}) as Record<string, JsonSchema>);
const required = computed<string[]>(() => (root.value.required as string[]) ?? []);

function setAtPath(target: Record<string, unknown>, path: string[], value: unknown): void {
  let cur: Record<string, unknown> | unknown[] = target;
  for (let i = 0; i < path.length - 1; i++) {
    const k = path[i];
    const key = cur as Record<string, unknown>;
    const nextIsIndex = /^\d+$/.test(path[i + 1]);
    if (key[k] == null || typeof key[k] !== 'object') key[k] = nextIsIndex ? [] : {};
    cur = key[k] as Record<string, unknown> | unknown[];
  }
  const last = path[path.length - 1];
  (cur as Record<string, unknown>)[last] = value;
}

function getArr(target: Record<string, unknown>, path: string[]): unknown[] {
  let cur: unknown = target;
  for (const k of path) cur = (cur as Record<string, unknown>)?.[k];
  return Array.isArray(cur) ? cur : [];
}

function commit(next: Record<string, unknown>): void {
  emit('update:modelValue', next);
}

function setValue(path: string[], value: unknown): void {
  const next = structuredClone(props.modelValue ?? {});
  setAtPath(next, path, value);
  commit(next);
}

function addToArray(path: string[]): void {
  const next = structuredClone(props.modelValue ?? {});
  const a = getArr(next, path);
  a.push({});
  commit(next);
}

function removeAt(arrayPath: string[], index: number): void {
  const next = structuredClone(props.modelValue ?? {});
  const a = getArr(next, arrayPath);
  a.splice(index, 1);
  commit(next);
}
</script>

<template>
  <div class="fui-schema-form">
    <FuiSchemaField
      v-for="(sub, key) in properties"
      :key="key"
      :schema="sub"
      :document="document"
      :field="String(key)"
      :required="required.includes(String(key))"
      :model-value="modelValue[String(key)]"
      :path="[String(key)]"
      @update="setValue"
      @add="addToArray"
      @remove="removeAt"
    />
    <p v-if="!Object.keys(properties).length" class="fui-schema-form__empty">{{ $t('common.notFound') }}</p>
  </div>
</template>

<style scoped>
.fui-schema-form__empty { color: var(--fui-text-tertiary); font-size: var(--fui-fs-sm); text-align: center; padding: var(--fui-sp-4); }
</style>
