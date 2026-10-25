<script setup lang="ts">
import { computed, ref } from 'vue';
import FuiSchemaField from './FuiSchemaField.vue';
import FuiField from './FuiField.vue';
import FuiSwitch from './FuiSwitch.vue';
import FuiRadio from './FuiRadio.vue';
import FuiRadioGroup from './FuiRadioGroup.vue';
import { deref, type DerefResult } from '@/lib/json/resolveSchema';
import type { JsonSchema } from './schemaTypes';

defineOptions({ name: 'FuiSchemaField' });

const props = defineProps<{ schema: JsonSchema; field: string; required?: boolean; modelValue?: unknown; path: string[]; document?: JsonSchema }>();
const emit = defineEmits<{
  (e: 'update', path: string[], value: unknown): void;
  (e: 'add', path: string[]): void;
  (e: 'remove', arrayPath: string[], index: number): void;
}>();

/**
 * A section document routes its shared types through `#/definitions/*` — S2's sections.json
 * contains 118 such references. Before this was resolved, a `$ref`-rooted property rendered as a
 * bare string input and its `required` list was silently dropped, so this was a correctness bug
 * under UM-MAN-001, not a nicety.
 */
const doc = computed<JsonSchema>(() => props.document ?? props.schema);
const eff = computed<JsonSchema>(() => deref(doc.value, props.schema).schema ?? props.schema);
const effItems = computed<DerefResult>(() => {
  const items = eff.value.items;
  if (!items || Array.isArray(items)) return { schema: null, recursive: false };
  return deref(doc.value, items as JsonSchema);
});

const type = computed(() => String(eff.value.type ?? 'string'));
const label = computed(() => eff.value.title || props.field);
const isLongText = computed(() => type.value === 'string' && !eff.value.enum && (eff.value.format === 'textarea' || ((eff.value.maxLength as number | undefined) ?? 0) > 80));
const enumOptions = computed(() => (eff.value.enum ?? []) as string[]);
const childProperties = computed<Record<string, JsonSchema>>(() => (eff.value.properties ?? {}) as Record<string, JsonSchema>);
const childRequired = computed<string[]>(() => (eff.value.required as string[]) ?? []);
const itemProperties = computed<Record<string, JsonSchema>>(() => (effItems.value.schema?.properties ?? {}) as Record<string, JsonSchema>);
const itemsAreObjects = computed(() => effItems.value.schema?.type === 'object' || !!effItems.value.schema?.properties);

const open = ref(false);

function set(v: unknown): void {
  emit('update', props.path, v);
}
const sub = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {});
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
</script>

<template>
  <!-- recursive definition: S2 shares these types, so a cycle is possible and marked, not followed -->
  <p v-if="effItems.recursive || (!eff.type && eff.unresolvedRef)" class="fui-sf-note">
    {{ $t('manual.schemaOpaque') }}
  </p>

  <!-- object -> collapsible group -->
  <div v-else-if="type === 'object'" class="fui-sf-group">
    <button class="fui-sf-group__head" :aria-expanded="open" @click="open = !open">
      <span>{{ label }}</span><span v-if="required" class="fui-sf-req">*</span><span aria-hidden="true">{{ open ? '▾' : '▸' }}</span>
    </button>
    <div v-show="open" class="fui-sf-group__body">
      <FuiSchemaField
        v-for="(child, key) in childProperties"
        :key="key"
        :schema="child"
        :document="doc"
        :field="String(key)"
        :required="childRequired.includes(String(key))"
        :model-value="sub(modelValue)[String(key)]"
        :path="[...path, String(key)]"
        @update="(p, v) => emit('update', p, v)"
        @add="(p) => emit('add', p)"
        @remove="(p, i) => emit('remove', p, i)"
      />
    </div>
  </div>

  <!-- array -> card list -->
  <div v-else-if="type === 'array'" class="fui-sf-arr">
    <div class="fui-sf-label">{{ label }}<span v-if="required" class="fui-sf-req">*</span></div>
    <div v-for="(item, i) in arr(modelValue)" :key="i" class="fui-sf-arr__card">
      <div class="fui-sf-arr__cardhead">
        <span>#{{ i + 1 }}</span>
        <button class="fui-sf-arr__del" @click="emit('remove', path, i)">{{ $t('common.delete') }}</button>
      </div>
      <template v-if="itemsAreObjects">
        <FuiSchemaField
          v-for="(child, key) in itemProperties"
          :key="key"
          :schema="child"
          :document="doc"
          :field="String(key)"
          :model-value="sub(item)[String(key)]"
          :path="[...path, String(i), String(key)]"
          @update="(p, v) => emit('update', p, v)"
          @add="(p) => emit('add', p)"
          @remove="(p, idx) => emit('remove', p, idx)"
        />
      </template>
      <FuiField
        v-else
        :model-value="String(item ?? '')"
        @update:model-value="(v: string) => emit('update', [...path, String(i)], v)"
      />
    </div>
    <button class="fui-sf-arr__add" @click="emit('add', path)">＋ {{ $t('manual.arrayAdd') }}</button>
  </div>

  <!-- enum -> radio chips -->
  <div v-else-if="type === 'string' && enumOptions.length" class="fui-sf-enum">
    <div class="fui-sf-label">{{ label }}<span v-if="required" class="fui-sf-req">*</span></div>
    <FuiRadioGroup :model-value="String(modelValue ?? '')" direction="horizontal" @update:model-value="(v: string) => set(v)">
      <FuiRadio v-for="opt in enumOptions" :key="opt" :name="opt" class="fui-sf-radio">{{ opt }}</FuiRadio>
    </FuiRadioGroup>
  </div>

  <!-- boolean -> switch -->
  <div v-else-if="type === 'boolean'" class="fui-sf-bool">
    <span class="fui-sf-label">{{ label }}</span>
    <FuiSwitch :model-value="Boolean(modelValue)" size="22" :label="label" @update:model-value="(v: boolean) => set(v)" />
  </div>

  <!-- number / integer -->
  <FuiField
    v-else-if="type === 'number' || type === 'integer'"
    :model-value="modelValue == null ? '' : String(modelValue)"
    :label="label + (required ? ' *' : '')"
    type="number"
    :placeholder="$t('common.optional')"
    @update:model-value="(v: string) => set(v === '' ? undefined : Number(v))"
  />

  <!-- long text -->
  <FuiField
    v-else-if="isLongText"
    :model-value="String(modelValue ?? '')"
    :label="label + (required ? ' *' : '')"
    type="textarea"
    rows="3"
    autosize
    @update:model-value="(v: string) => set(v)"
  />

  <!-- default string -->
  <FuiField
    v-else
    :model-value="String(modelValue ?? '')"
    :label="label + (required ? ' *' : '')"
    :placeholder="$t('common.optional')"
    @update:model-value="(v: string) => set(v)"
  />
</template>

<style scoped>
.fui-sf-note { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.fui-sf-label { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin-bottom: 4px; }
.fui-sf-req { color: var(--fui-danger); margin-left: 2px; }
.fui-sf-group { border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); margin-bottom: var(--fui-sp-3); overflow: hidden; }
.fui-sf-group__head { width: 100%; text-align: left; background: var(--fui-surface-2); border: 0; padding: var(--fui-sp-3) var(--fui-sp-4); display: flex; align-items: center; gap: 4px; font-weight: 600; min-height: var(--fui-tap-min); }
.fui-sf-group__head span:last-child { margin-left: auto; }
.fui-sf-group__body { padding: var(--fui-sp-3) var(--fui-sp-4); }
.fui-sf-arr__card { border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); padding: var(--fui-sp-3); margin-bottom: var(--fui-sp-2); }
.fui-sf-arr__cardhead { display: flex; justify-content: space-between; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-bottom: 4px; }
.fui-sf-arr__del { border: 0; background: none; color: var(--fui-danger); font-size: var(--fui-fs-xs); }
.fui-sf-arr__add { width: 100%; min-height: var(--fui-tap-min); border: 1px dashed var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); color: var(--fui-primary); }
.fui-sf-enum { margin-bottom: var(--fui-sp-3); }
.fui-sf-radio { margin: 0 12px 8px 0; }
.fui-sf-bool { display: flex; justify-content: space-between; align-items: center; padding: var(--fui-sp-3) 0; }
</style>
