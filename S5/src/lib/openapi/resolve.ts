import type { ApiDocRoot } from './parseDoc';

/**
 * UM-CAP-003 `$ref` resolution and schema-tree building.
 *
 * `src/lib/json/resolveSchema.ts` already does this for the JSON-Schema-flavoured S2 section
 * schemas: it knows `#/definitions/*`, and it returns a *merged schema* rather than a renderable
 * tree. This module keeps that discipline (local pointers only, memoisation, an in-progress set for
 * cycles) and adds what a document viewer needs:
 *
 *  - **two independent caps**, because they catch different documents: `maxDepth` (8) stops a
 *    pathological nesting chain, `maxNodes` (400) stops a wide schema graph whose depth is legal.
 *    Hitting either yields a visible marker node, never a silent truncation;
 *  - **3.1 `$ref` with sibling keywords**: a `$ref` beside `description`/`type`/`nullable` is legal,
 *    the reference supplies the shape and the siblings override it;
 *  - **lazy materialisation**: a node knows how to produce its own children but does not do so until
 *    asked, which is what makes "expand arbitrary nesting" affordable on a large document.
 *
 * Absolute (`scheme://`) or foreign-file pointers are never dereferenced — the viewer must not reach
 * outside the artifact it was handed (UM-IF-001, UM-REV-005).
 */

/** Nesting levels expanded before a node reports 「已达深度上限」. */
export const DEPTH_CAP = 8;
/** Total nodes one resolver materialises before a node reports 「已达展开上限」. */
export const NODE_CAP = 400;
/** Hops a single `$ref` chain (`$ref` onto a node that itself has `$ref`) may take. */
const MAX_HOPS = 24;

export type NodeMarker = '' | 'recursive' | 'depth' | 'nodeCap' | 'unresolved';
export type NodeRole = 'root' | 'body' | 'response' | 'parameter' | 'property' | 'item' | 'additional' | 'variant';

export interface SchemaNode {
  name: string;
  role: NodeRole;
  /** JSON pointer of the resolved schema, e.g. `#/components/schemas/Pet/properties/name`. */
  pointer: string;
  /** The `$ref` followed to get here, verbatim; `''` when the schema is inline. */
  ref: string;
  /** Every `$ref` resolved on the path to this node — the cycle guard a lazy caller inherits. */
  refChain: string[];
  depth: number;
  types: string[];
  format: string;
  required: boolean;
  description: string;
  enumValues: unknown[];
  hasDefault: boolean;
  defaultValue: unknown;
  hasExample: boolean;
  exampleValue: unknown;
  /** `key: value` chips (minLength, pattern, readOnly, …): document data, so not translated. */
  constraints: string[];
  /** References merged in by `allOf`, shown so a reader knows the node is wider than it looks. */
  composeRefs: string[];
  marker: NodeMarker;
  /** True when expanding this node could produce children. */
  expandable: boolean;
  loaded: boolean;
  children: SchemaNode[];
  /** The schema object behind this node, kept so expansion stays lazy. Never rendered. */
  source: unknown;
}

export interface NodeOptions {
  role?: NodeRole;
  required?: boolean;
  pointer?: string;
  depth?: number;
  refChain?: string[];
}

export interface ResolveOptions {
  maxDepth?: number;
  maxNodes?: number;
}

export interface ResolvedRef {
  ok: boolean;
  schema: Record<string, unknown> | null;
  /** Normalised pointer (`#/…`) so it can serve as a cache key and a display id. */
  pointer: string;
}

export interface SchemaResolver {
  readonly root: ApiDocRoot;
  readonly maxDepth: number;
  readonly maxNodes: number;
  /** Nodes materialised so far across every lazy expansion of this document. */
  used(): number;
  exhausted(): boolean;
  resolveRef(ref: string): ResolvedRef;
  /** One node: `$ref` followed, `allOf` merged, no children yet. */
  node(name: string, schema: unknown, opts?: NodeOptions): SchemaNode;
  /** The node's children, materialised once and memoised; caps and cycles are decided here. */
  childrenOf(node: SchemaNode): SchemaNode[];
  /** A whole subtree, expanded eagerly under a fresh budget. Used by tests and "expand branch". */
  subtree(name: string, schema: unknown, opts?: NodeOptions): SchemaNode;
  /** Forget every memoised child list, e.g. when a document is swapped out. */
  reset(): void;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return isRecord(v) ? v : null;
}

function encodeToken(token: string): string {
  return token.replace(/~/g, '~0').replace(/\//g, '~1');
}

function decodeToken(token: string): string {
  return token.replace(/~1/g, '/').replace(/~0/g, '~');
}

/** The last segment of a pointer, as a component name: `#/components/schemas/Pet` -> `Pet`. */
export function schemaNameOf(ref: string): string {
  const parts = ref.split('/');
  return decodeToken(parts[parts.length - 1] ?? '');
}

/**
 * Same-document JSON pointer walk. Accepts `#/components/schemas/X`, `#/definitions/X`, a rooted
 * `/a/b` pointer and `#` itself; a pointer carrying a scheme is rejected rather than fetched.
 */
export function pointerToNode(root: ApiDocRoot, pointer: string): unknown {
  if (pointer.includes('://')) return null;
  const clean = pointer.startsWith('#') ? pointer.slice(1) : pointer;
  if (!clean) return root;
  let current: unknown = root;
  for (const raw of clean.split('/')) {
    if (!raw) continue;
    const token = decodeToken(raw);
    if (Array.isArray(current)) {
      const index = Number(token);
      if (!Number.isInteger(index) || index < 0 || index >= current.length) return null;
      current = current[index];
      continue;
    }
    if (!isRecord(current)) return null;
    current = current[token];
  }
  return current;
}

function locate(root: ApiDocRoot, ref: string): { pointer: string; schema: unknown } | null {
  if (!ref) return null;
  if (ref.startsWith('#') || ref.startsWith('/')) {
    const pointer = ref.startsWith('#') ? ref : `#${ref}`;
    return { pointer, schema: pointerToNode(root, pointer) };
  }
  // A bare component name is not OpenAPI, but hand-edited documents do it. `components.schemas`
  // first, then `definitions`, which is where the S2 section schemas live.
  for (const candidate of [`#/components/schemas/${ref}`, `#/definitions/${ref}`]) {
    const found = pointerToNode(root, candidate);
    if (found !== undefined && found !== null) return { pointer: candidate, schema: found };
  }
  return null;
}

/** Every `$ref` string inside a schema, bounded. The search index and the ref chips both need it. */
export function collectRefs(schema: unknown, cap = 200): string[] {
  const out: string[] = [];
  const stack: unknown[] = [schema];
  const seen = new Set<unknown>();
  while (stack.length && out.length < cap) {
    const current = stack.pop();
    if (Array.isArray(current)) {
      for (const item of current) stack.push(item);
      continue;
    }
    if (!isRecord(current) || seen.has(current)) continue;
    seen.add(current);
    if (typeof current.$ref === 'string') out.push(current.$ref);
    for (const value of Object.values(current)) if (value && typeof value === 'object') stack.push(value);
  }
  return out;
}

function typesOf(shape: Record<string, unknown>): string[] {
  const raw = shape.type;
  if (typeof raw === 'string') return [raw];
  if (Array.isArray(raw)) {
    const list = raw.filter((t): t is string => typeof t === 'string');
    if (list.length) return [...new Set(list)];
  }
  return [];
}

const NUMBER_CHIPS = ['minLength', 'maxLength', 'pattern', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minItems', 'maxItems', 'minProperties', 'maxProperties'];

function constraintChips(shape: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const key of NUMBER_CHIPS) {
    const value = shape[key];
    if (value !== undefined && value !== null && value !== false) out.push(`${key}: ${String(value)}`);
  }
  if (shape.uniqueItems === true) out.push('uniqueItems');
  if (shape.readOnly === true) out.push('readOnly');
  if (shape.writeOnly === true) out.push('writeOnly');
  if (shape.deprecated === true) out.push('deprecated');
  if ('const' in shape) out.push(`const: ${JSON.stringify(shape.const) ?? 'null'}`);
  if (shape.additionalProperties === false) out.push('additionalProperties: false');
  return out;
}

/** The label of a `oneOf`/`anyOf` member: title, then component name, then type, then position. */
function variantLabel(member: unknown, index: number): string {
  const record = asRecord(member);
  if (!record) return `#${index + 1}`;
  if (typeof record.title === 'string' && record.title) return record.title;
  if (typeof record.$ref === 'string') return schemaNameOf(record.$ref);
  const types = typesOf(record);
  return types[0] ?? `#${index + 1}`;
}

/** 3.1: `$ref` plus siblings — the reference provides the shape, the siblings override it. */
function applySiblings(target: Record<string, unknown>, withRef: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...target };
  for (const [key, value] of Object.entries(withRef)) {
    if (key === '$ref') continue;
    out[key] = value;
  }
  return out;
}

/** Merge one `allOf` member into the accumulated shape without inventing anything. */
function mergeInto(target: Record<string, unknown>, member: Record<string, unknown>): void {
  for (const [key, value] of Object.entries(member)) {
    const mine = target[key];
    if (key === 'properties' && isRecord(mine) && isRecord(value)) {
      // The member's declarations come first, the accumulator's win: order is declaration order.
      target[key] = { ...value, ...mine };
      continue;
    }
    if (key === 'required' && Array.isArray(mine) && Array.isArray(value)) {
      target[key] = [...new Set([...(value as unknown[]), ...(mine as unknown[])])];
      continue;
    }
    if (key === 'enum' && Array.isArray(mine) && Array.isArray(value)) {
      // An intersection is the honest answer; a union would offer values the API never accepts.
      target[key] = (mine as unknown[]).filter((v) => (value as unknown[]).some((x) => x === v));
      continue;
    }
    if (key === 'type') {
      const ours = typesOf(target)[0];
      const theirs = typesOf(member)[0];
      if (ours && theirs && ours !== theirs) target[key] = [...new Set([ours, theirs])];
      else if (!ours && theirs) target[key] = theirs;
      continue;
    }
    if (key === 'description' && typeof mine === 'string' && mine && typeof value === 'string') continue;
    if (mine === undefined) target[key] = value;
  }
}

interface Followed {
  shape: Record<string, unknown>;
  ref: string;
  pointer: string;
  chain: string[];
  marker: NodeMarker;
  composeRefs: string[];
}

export function createResolver(root: ApiDocRoot, options: ResolveOptions = {}): SchemaResolver {
  const maxDepth = options.maxDepth ?? DEPTH_CAP;
  const maxNodes = options.maxNodes ?? NODE_CAP;
  const refCache = new Map<string, ResolvedRef>();
  const childCache = new Map<string, SchemaNode[]>();
  let used = 0;

  function resolveRef(ref: string): ResolvedRef {
    const cached = refCache.get(ref);
    if (cached) return cached;
    const found = locate(root, ref);
    const schema = found && isRecord(found.schema) ? found.schema : null;
    const result: ResolvedRef = { ok: !!schema, schema, pointer: found ? found.pointer : ref };
    refCache.set(ref, result);
    return result;
  }

  /** Follow the `$ref` chain, then merge `allOf`, arriving at one shape worth rendering. */
  function follow(schema: unknown, chain: string[]): Followed {
    let current = asRecord(schema);
    let ref = '';
    let pointer = '';
    let outChain = chain;
    let marker: NodeMarker = '';
    const composeRefs: string[] = [];
    let hops = 0;

    while (current && typeof current.$ref === 'string' && hops++ < MAX_HOPS) {
      const target = current.$ref;
      ref = target;
      if (outChain.includes(target)) {
        // Cycle: report the reference and the type it points at, and stop. Expanding it again is
        // what makes a recursive schema unbounded.
        marker = 'recursive';
        const hit = resolveRef(target);
        pointer = hit.pointer;
        const self = Object.keys(current).length > 1 && hit.schema ? applySiblings(hit.schema, current) : hit.schema ?? current;
        return { shape: self, ref, pointer, chain: outChain, marker, composeRefs };
      }
      const resolved = resolveRef(target);
      if (!resolved.ok || !resolved.schema) {
        marker = 'unresolved';
        // Keep the siblings: an unresolvable pointer still carries its own description and type.
        return { shape: current, ref, pointer, chain: outChain, marker, composeRefs };
      }
      pointer = resolved.pointer;
      outChain = [...outChain, target];
      current = Object.keys(current).length > 1 ? applySiblings(resolved.schema, current) : resolved.schema;
    }

    if (!current) {
      // `additionalProperties: false`, a boolean schema, or a pointer that landed on a scalar.
      if (typeof schema === 'boolean') return { shape: { type: 'boolean' }, ref, pointer, chain: outChain, marker, composeRefs };
      return { shape: {}, ref, pointer, chain: outChain, marker: marker || 'unresolved', composeRefs };
    }

    if (Array.isArray(current.allOf)) {
      const merged: Record<string, unknown> = { ...current };
      delete merged.allOf;
      const members = current.allOf as unknown[];
      let memberHops = 0;
      for (const raw of members) {
        if (memberHops++ >= MAX_HOPS) {
          marker = marker || 'depth';
          break;
        }
        const inner = follow(raw, outChain);
        composeRefs.push(inner.ref ? refLabel(inner.ref) : '(inline)');
        if (inner.marker) marker = marker || inner.marker;
        outChain = inner.chain;
        mergeInto(merged, inner.shape);
      }
      return { shape: merged, ref, pointer, chain: outChain, marker, composeRefs };
    }

    return { shape: current, ref, pointer, chain: outChain, marker, composeRefs };
  }

  function couldExpand(shape: Record<string, unknown>): boolean {
    if (isRecord(shape.properties) && Object.keys(shape.properties).length) return true;
    if (isRecord(shape.additionalProperties)) return true;
    if (shape.items !== undefined && shape.items !== null) return true;
    if (Array.isArray(shape.prefixItems) && shape.prefixItems.length) return true;
    if (Array.isArray(shape.oneOf) && shape.oneOf.length) return true;
    if (Array.isArray(shape.anyOf) && shape.anyOf.length) return true;
    return false;
  }

  function blankNode(name: string, opts: NodeOptions, chain: string[], marker: NodeMarker, extra: Partial<SchemaNode>): SchemaNode {
    const shape = (extra.source && isRecord(extra.source) ? extra.source : {}) as Record<string, unknown>;
    return {
      name,
      role: opts.role ?? 'property',
      pointer: extra.pointer ?? opts.pointer ?? `inline/${opts.depth ?? 0}/${encodeToken(name)}`,
      ref: extra.ref ?? '',
      refChain: chain,
      depth: opts.depth ?? 0,
      types: typesOf(shape),
      format: typeof shape.format === 'string' ? shape.format : '',
      required: !!opts.required,
      description: typeof shape.description === 'string' ? shape.description : '',
      enumValues: Array.isArray(shape.enum) ? [...(shape.enum as unknown[])] : [],
      hasDefault: 'default' in shape,
      defaultValue: shape.default,
      hasExample: 'example' in shape,
      exampleValue: shape.example,
      constraints: constraintChips(shape),
      composeRefs: [],
      marker,
      expandable: false,
      loaded: true,
      children: [],
      source: extra.source ?? shape,
    };
  }

  function node(name: string, schema: unknown, opts: NodeOptions = {}): SchemaNode {
    const depth = opts.depth ?? 0;
    const chain = opts.refChain ?? [];

    if (used >= maxNodes) {
      used++;
      const ref = typeof asRecord(schema)?.$ref === 'string' ? String(asRecord(schema)?.$ref) : '';
      return blankNode(name, opts, chain, 'nodeCap', { ref });
    }
    used++;

    const followed = follow(schema, chain);
    const hasKids = couldExpand(followed.shape);
    const capped = depth >= maxDepth && hasKids;
    const marker: NodeMarker = followed.marker || (capped ? 'depth' : '');
    const expandable = hasKids && !marker;

    const base = blankNode(name, opts, followed.chain, marker, { source: followed.shape, ref: followed.ref, pointer: followed.pointer || opts.pointer || `inline/${depth}/${encodeToken(name)}` });
    base.composeRefs = followed.composeRefs.filter((r) => r && r !== '(inline)');
    base.expandable = expandable;
    base.loaded = !expandable;
    return base;
  }

  function childrenOf(parent: SchemaNode): SchemaNode[] {
    const cacheKey = `${parent.pointer || 'inline'}@${parent.depth}`;
    const cached = childCache.get(cacheKey);
    if (cached) {
      parent.children = cached;
      parent.loaded = true;
      return cached;
    }
    if (!parent.expandable) {
      parent.children = [];
      parent.loaded = true;
      return parent.children;
    }

    const shape = asRecord(parent.source) ?? {};
    const nextDepth = parent.depth + 1;
    const requiredSet = new Set(Array.isArray(shape.required) ? (shape.required as unknown[]).map(String) : []);
    const out: SchemaNode[] = [];
    const base = parent.pointer || '#';

    const variantKey = Array.isArray(shape.oneOf) && shape.oneOf.length ? 'oneOf' : Array.isArray(shape.anyOf) && shape.anyOf.length ? 'anyOf' : '';
    if (variantKey) {
      const members = shape[variantKey] as unknown[];
      members.forEach((member, index) => {
        out.push(node(variantLabel(member, index), member, { role: 'variant', pointer: `${base}/${variantKey}/${index}`, depth: nextDepth, refChain: parent.refChain }));
      });
    }

    const properties = asRecord(shape.properties);
    if (properties) {
      for (const [key, child] of Object.entries(properties)) {
        out.push(node(key, child, { role: 'property', required: requiredSet.has(key), pointer: `${base}/properties/${encodeToken(key)}`, depth: nextDepth, refChain: parent.refChain }));
      }
    }

    const tuple = Array.isArray(shape.prefixItems) ? (shape.prefixItems as unknown[]) : Array.isArray(shape.items) ? (shape.items as unknown[]) : null;
    if (tuple) {
      tuple.forEach((member, index) => {
        out.push(node(`[${index}]`, member, { role: 'item', pointer: `${base}/prefixItems/${index}`, depth: nextDepth, refChain: parent.refChain }));
      });
    } else if (shape.items !== undefined && !Array.isArray(shape.items)) {
      out.push(node('items', shape.items, { role: 'item', pointer: `${base}/items`, depth: nextDepth, refChain: parent.refChain }));
    }

    if (isRecord(shape.additionalProperties)) {
      out.push(node('additionalProperties', shape.additionalProperties, { role: 'additional', pointer: `${base}/additionalProperties`, depth: nextDepth, refChain: parent.refChain }));
    }

    childCache.set(cacheKey, out);
    parent.children = out;
    parent.loaded = true;
    return out;
  }

  function fill(head: SchemaNode): void {
    if (head.loaded) return;
    for (const child of childrenOf(head)) fill(child);
  }

  function subtree(name: string, schema: unknown, opts: NodeOptions = {}): SchemaNode {
    const head = node(name, schema, opts);
    fill(head);
    return head;
  }

  const resolver: SchemaResolver = {
    root,
    maxDepth,
    maxNodes,
    used: () => used,
    exhausted: () => used >= maxNodes,
    resolveRef,
    node,
    childrenOf,
    subtree,
    reset() {
      childCache.clear();
      used = 0;
    },
  };
  return resolver;
}

/** The label a viewer shows for a `$ref`: the component name, not the whole pointer. */
export function refLabel(ref: string): string {
  if (!ref) return '';
  if (ref.includes('#') || ref.includes('/')) return schemaNameOf(ref);
  return ref;
}
