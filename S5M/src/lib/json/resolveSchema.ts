import type { JsonSchema } from '@/fui/schemaTypes';

/**
 * Shared `$ref` resolution for the S2 section schemas (UM-CAP-001 completion, UM-MAN-001 form).
 *
 * S1 hands S5-M a *self-contained* Draft 2020-12 document per section
 * (`SectionSchemaView`: "the self-contained Draft 2020-12 document, passed through as-is"), and S2
 * inlines every definition — `sections.json` routes 118 references through `#/definitions/*`. So
 * the only pointers that must resolve are local, and no network resolver is wanted: a form or an
 * editor that quietly dereferenced an absolute schema URI would be a privacy leak and a first-load
 * stall.
 *
 * Before this existed `FuiSchemaField` ignored `$ref` entirely, so any `$ref`-rooted property
 * rendered as a bare string input and its `required` list was silently dropped.
 */

/** Depth cap for `allOf/anyOf` merging and nested `$ref` hops. A real section never reaches it. */
const MAX_HOPS = 24;

function isSchema(v: unknown): v is JsonSchema {
  return !!v && typeof v === 'object';
}

/** `#/definitions/name`, `#/properties/a/properties/b`, `#/$defs/name`, or bare `#`. */
export function pointerToNode(root: JsonSchema, ref: string): JsonSchema | null {
  if (!ref || ref === '#') return root;
  if (!ref.startsWith('#')) return null; // external: deliberately unsupported
  const parts = ref
    .slice(1)
    .split('/')
    .filter((p) => p !== '')
    .map((p) => p.replace(/~1/g, '/').replace(/~0/g, '~'));
  let cur: unknown = root;
  for (const part of parts) {
    if (!cur || typeof cur !== 'object') return null;
    cur = (cur as Record<string, unknown>)[part];
  }
  return isSchema(cur) ? cur : null;
}

/**
 * Follow `$ref` (and merge `allOf` object members) until a node with usable keywords is reached.
 * `seen` breaks reference cycles: `localisedText` and `businessKey` reference siblings, and a
 * recursive schema must produce a finite, marked result rather than blow the stack.
 */
export function deref(root: JsonSchema, node: JsonSchema | null | undefined, seen: Set<string> = new Set()): DerefResult {
  if (!isSchema(node)) return { schema: null, recursive: false };
  let current: JsonSchema = node;
  let recursive = false;
  let hops = 0;

  while (typeof current.$ref === 'string' && hops < MAX_HOPS) {
    if (seen.has(current.$ref)) {
      recursive = true;
      break;
    }
    seen.add(current.$ref);
    const next = pointerToNode(root, current.$ref);
    if (!next) {
      // An unresolvable pointer is a data-quality answer, not a crash: render it as an opaque
      // object so the reviewer can see the reference that S1/S2 failed to inline.
      return { schema: { type: 'object', unresolvedRef: current.$ref }, recursive: false };
    }
    current = next;
    hops++;
  }

  const members = Array.isArray(current.allOf) ? (current.allOf as JsonSchema[]) : [];
  if (members.length) {
    const merged: JsonSchema = { ...current, properties: { ...(current.properties ?? {}) } };
    delete merged.allOf;
    for (const member of members) {
      const sub = deref(root, member, new Set(seen));
      if (!sub.schema) continue;
      recursive = recursive || sub.recursive;
      Object.assign(merged, stripConflict(sub.schema, merged));
    }
    return { schema: merged, recursive };
  }

  return { schema: current, recursive };
}

/** Merge a sub-schema into a target without clobbering keys the target already declares. */
function stripConflict(sub: JsonSchema, into: JsonSchema): JsonSchema {
  const out: JsonSchema = { ...sub };
  if (into.properties && sub.properties) {
    out.properties = { ...sub.properties, ...into.properties };
  }
  if (into.required && sub.required) {
    out.required = [...new Set([...into.required, ...sub.required])];
  }
  return out;
}

/** The schema of one object property, resolved. */
export function propertySchema(root: JsonSchema, parent: JsonSchema | null, key: string): DerefResult {
  const props = parent?.properties ?? (parent?.patternProperties as Record<string, JsonSchema> | undefined);
  const raw = props?.[key];
  if (!raw) {
    // `additionalProperties` object schemas are common in S2 keyed lists.
    const extra = parent?.additionalProperties;
    if (isSchema(extra)) return deref(root, extra as JsonSchema);
    return { schema: null, recursive: false };
  }
  return deref(root, raw);
}

/** The schema of array items, resolved. */
export function itemsSchema(root: JsonSchema, parent: JsonSchema | null): DerefResult {
  if (!parent?.items) return { schema: null, recursive: false };
  const raw = Array.isArray(parent.items) ? parent.items[0] : parent.items;
  return deref(root, raw as JsonSchema);
}

/** JSON Pointer (`/entities/0/name`) to the schema that validates it. */
export function schemaAtPointer(root: JsonSchema, pointer: string): DerefResult {
  const tokens = pointer.split('/').slice(1).map((t) => t.replace(/~1/g, '/').replace(/~0/g, '~'));
  let node: JsonSchema = root;
  let result: DerefResult = { schema: node, recursive: false };
  for (const token of tokens) {
    const cur = result.schema;
    if (!cur) return { schema: null, recursive: false };
    if (/^\d+$/.test(token)) {
      result = itemsSchema(root, cur);
    } else {
      result = propertySchema(root, cur, token);
    }
    if (!result.schema) return result;
    node = result.schema;
  }
  return result;
}

/**
 * Enum values as completion candidates, in declaration order. Empty for a non-enum node.
 */
export function enumCandidates(schema: JsonSchema | null): string[] {
  if (!schema || !Array.isArray(schema.enum)) return [];
  return schema.enum.filter((v) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean').map(String);
}

/** Property names offered at an object node, minus the ones already present in the document. */
export function keyCandidates(schema: JsonSchema | null, present: ReadonlySet<string>): { name: string; schema: JsonSchema | null; required: boolean }[] {
  const props = schema?.properties;
  if (!props) return [];
  const required = new Set(schema?.required ?? []);
  return Object.entries(props)
    .filter(([name]) => !present.has(name))
    .map(([name, sub]) => ({ name, schema: sub as JsonSchema, required: required.has(name) }));
}

export interface DerefResult {
  schema: JsonSchema | null;
  /** True when the chain came back around — callers must render a marker, not recurse. */
  recursive: boolean;
}
