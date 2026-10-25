import type { ApiDocRoot } from './parseDoc';
import { createResolver, DEPTH_CAP, NODE_CAP } from './resolve';
import type { SchemaNode, SchemaResolver } from './resolve';

/**
 * UM-CAP-003 example synthesis.
 *
 * S2 keeps `requestExample` / `responseExample` in `api-reference.md` and `api/examples/*`, and no
 * endpoint in the current S1 contract can serve those files (`contract/openapi.json` has no
 * `package-files/{path}` read). The document itself carries only schemas. So the viewer *derives*
 * the example from the schema, and says so on screen — an example presented as if it came from the
 * artifact would be a fabricated contract detail.
 *
 * Determinism is a requirement, not a preference: the same document must produce the same example
 * for a test to assert on it and for a reader not to see values shift under them. No clock, no
 * randomness, no iteration order that a engine could change — hence the fixed fillers below and the
 * explicit `required`-first sort.
 *
 * Precedence at every level: declared `example` > `default` > `enum[0]` > `const` > type-shaped
 * filler (format-aware). Arrays get exactly one item; objects get required keys first.
 */

/** Fixed sample values, one per JSON type — the same table S2 uses, so both sides agree. */
const TYPE_VALUES: Record<string, unknown> = {
  string: 'sample',
  number: 1.5,
  integer: 1,
  boolean: true,
  null: null,
};

/** Format-aware string fillers. None of them is an absolute address on purpose. */
const FORMAT_VALUES: Record<string, unknown> = {
  date: '2024-01-01',
  'date-time': '2024-01-01T00:00:00Z',
  time: '12:00:00',
  duration: 'PT1H',
  email: 'user@example.com',
  hostname: 'example.org',
  idnName: 'example.org',
  uuid: '00000000-0000-0000-0000-000000000000',
  'uri-reference': '//example.org/resource',
  uri: '//example.org/resource',
  iri: '//example.org/resource',
  ipv4: '127.0.0.1',
  ipv6: '::1',
  byte: 'c2FtcGxl',
  binary: '(binary)',
  password: 'sample',
};

export interface ExampleOptions {
  maxDepth?: number;
  /** Keys an object contributes before the rest are dropped as truncated. */
  maxKeys?: number;
  /** Reuse a resolver (and its memoisation) across calls; a fresh one is created otherwise. */
  resolver?: SchemaResolver;
}

export interface ExampleResult {
  value: unknown;
  /** True when something could not be produced: a cycle, a cap, or an unresolvable reference. */
  truncated: boolean;
  /** Why synthesis stopped, as a stable token for the view (`''` when it did not). */
  stopped: '' | 'recursive' | 'depth' | 'nodeCap' | 'unresolved';
}

function numericValue(fallback: number, schema: Record<string, unknown>): number {
  const min = typeof schema.minimum === 'number' ? schema.minimum : null;
  const max = typeof schema.maximum === 'number' ? schema.maximum : null;
  if (min !== null && fallback < min) return min;
  if (max !== null && fallback > max) return max;
  return fallback;
}

function stringValue(node: SchemaNode): string {
  const declared = node.format ? FORMAT_VALUES[node.format] : undefined;
  let value = typeof declared === 'string' ? declared : String(TYPE_VALUES.string);
  const source = (node.source ?? {}) as Record<string, unknown>;
  const minLength = typeof source.minLength === 'number' ? source.minLength : 0;
  if (value.length < minLength) value = value.padEnd(minLength, 'x');
  // A `pattern` is *not* honoured: running a document-supplied regular expression on the render
  // path is a risk this viewer does not need, and the constraint is shown on the schema node.
  return value;
}

/**
 * The type to synthesise for. Declaration order matters: `properties` without `type` is legal and
 * common, and guessing `string` there would hide an entire object body.
 */
function primaryType(node: SchemaNode): string {
  const nonNull = node.types.filter((t) => t !== 'null');
  if (nonNull.length) return nonNull[0] ?? '';
  if (node.types.length) return node.types[0] ?? '';
  const source = (node.source ?? {}) as Record<string, unknown>;
  if (source.items !== undefined || Array.isArray(source.prefixItems)) return 'array';
  if (source.properties !== undefined || isRecordLike(source.additionalProperties)) return 'object';
  if (Array.isArray(source.oneOf) || Array.isArray(source.anyOf)) return primaryVariant(source);
  if (node.enumValues.length) return typeof node.enumValues[0] === 'number' ? 'number' : 'string';
  if (node.role === 'item') return 'array';
  return 'string';
}

function isRecordLike(value: unknown): boolean {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/** `oneOf`/`anyOf` are alternatives; the first is the example, and the rest stay visible in the tree. */
function primaryVariant(source: Record<string, unknown>): string {
  const members = (Array.isArray(source.oneOf) ? source.oneOf : (source.anyOf as unknown[])) ?? [];
  const first = members[0];
  if (!isRecordLike(first)) return 'object';
  const record = first as Record<string, unknown>;
  if (record.type !== undefined) return Array.isArray(record.type) ? String(record.type[0]) : String(record.type);
  return record.properties !== undefined ? 'object' : record.items !== undefined ? 'array' : 'object';
}

function build(resolver: SchemaResolver, node: SchemaNode, depth: number, maxDepth: number, maxKeys: number, state: { truncated: boolean; stopped: ExampleResult['stopped'] }): unknown {
  if (node.marker === 'recursive' || node.marker === 'nodeCap' || node.marker === 'unresolved') {
    state.truncated = true;
    if (!state.stopped) state.stopped = node.marker;
    return undefined;
  }
  if (node.hasExample) return node.exampleValue;
  if (node.hasDefault) return node.defaultValue;
  if (node.enumValues.length) return node.enumValues[0];

  // Materialise once: `source` has already had `$ref` followed and `allOf` merged by the resolver,
  // so this walk never re-resolves and never re-counts budget for a branch it has seen.
  if (node.expandable && !node.loaded) resolver.childrenOf(node);
  if (node.marker === 'depth') {
    state.truncated = true;
    if (!state.stopped) state.stopped = 'depth';
  }

  const type = primaryType(node);
  const source = (node.source ?? {}) as Record<string, unknown>;
  if (type === 'null') return null;

  // `oneOf`/`anyOf` have no single shape: the first alternative is the example, and the rest of
  // them stay visible in the tree next to it.
  const properties = node.children.filter((c) => c.role === 'property');
  const variants = node.children.filter((c) => c.role === 'variant');
  if (!properties.length && variants[0]) return build(resolver, variants[0], depth + 1, maxDepth, maxKeys, state);

  if (type === 'object' && !properties.length && depth >= maxDepth) return {};

  if (type === 'object') {
    // Required first, then declaration order — `Array.prototype.sort` is stable in every engine we
    // target, so the tie-break is the document's own key order.
    const ordered = [...properties].sort((a, b) => Number(b.required) - Number(a.required));
    const picked = ordered.slice(0, maxKeys);
    if (ordered.length > picked.length) state.truncated = true;
    const out: Record<string, unknown> = {};
    for (const child of picked) {
      const value = build(resolver, child, depth + 1, maxDepth, maxKeys, state);
      // A required key whose value could not be produced stays visible as `null`: dropping it would
      // hand back an example that could not validate against its own schema.
      if (value !== undefined) out[child.name] = value;
      else if (child.required) out[child.name] = null;
    }
    return out;
  }

  if (type === 'array') {
    const items = node.children.filter((c) => c.role === 'item');
    const tuple = items.filter((c) => /^\[\d+\]$/.test(c.name));
    if (tuple.length) {
      return tuple.map((c) => build(resolver, c, depth + 1, maxDepth, maxKeys, state)).filter((v) => v !== undefined);
    }
    const first = items[0];
    if (!first) {
      // `items` absent means an unconstrained array; an empty one is the only honest example.
      state.truncated = true;
      return [];
    }
    // Exactly one item: a reader sees the element shape without the example lying about volume.
    const value = build(resolver, first, depth + 1, maxDepth, maxKeys, state);
    return value === undefined ? [] : [value];
  }

  if (type === 'integer') return Math.round(numericValue(Number(TYPE_VALUES.integer), source));
  if (type === 'number') return numericValue(Number(TYPE_VALUES.number), source);
  if (type === 'boolean') return Boolean(TYPE_VALUES.boolean);
  return stringValue(node);
}

/**
 * Derive an example for one schema. `root` is the document (for `$ref` resolution); the reference
 * cycle guard is the resolver's, so a recursive schema yields a marked, finite example.
 */
export function exampleFromSchema(root: ApiDocRoot, schema: unknown, options: ExampleOptions = {}): ExampleResult {
  const maxDepth = options.maxDepth ?? DEPTH_CAP;
  const maxKeys = options.maxKeys ?? 24;
  const resolver = options.resolver ?? createResolver(root, { maxDepth, maxNodes: NODE_CAP });
  const state = { truncated: false, stopped: '' as ExampleResult['stopped'] };
  const head = resolver.node('example', schema, { role: 'root', depth: 0 });
  const value = build(resolver, head, 0, maxDepth, maxKeys, state);
  return { value: value === undefined ? null : value, truncated: state.truncated, stopped: state.stopped };
}

/** The same value, as the JSON text a reader can copy. `undefined` keys are dropped by the engine. */
export function exampleAsText(result: ExampleResult): string {
  try {
    return JSON.stringify(result.value, null, 2) ?? 'null';
  } catch {
    // A circular structure cannot come out of `build`, but a document that smuggled one in via a
    // declared `example` would; say so rather than throwing through the render.
    return '{"unrepresentable":true}';
  }
}
