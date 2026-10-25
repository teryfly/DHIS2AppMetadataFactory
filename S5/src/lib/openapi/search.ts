/**
 * UM-CAP-003 full-text search over an OpenAPI document.
 *
 * The requirement is 全文搜索 over what a reader can actually see: `operationId`, summary,
 * description, path, tag names and schema names. It is deliberately *not* a search of file
 * contents or of the target instance — it indexes one already-parsed artifact.
 *
 * Everything is case-folded once, at index build time, so a keystroke is a `includes()` over small
 * strings rather than a re-walk of the document. Debounce (200 ms) belongs to the caller: this is
 * pure and synchronous so a test can call it directly and a component can be timed honestly.
 */

/** The subset of an operation the index reads; `ApiOperation` from `./docModel` satisfies it. */
export interface SearchableOperation {
  id: string;
  path: string;
  method: string;
  summary: string;
  description: string;
  tags: string[];
  schemaRefs: string[];
  registryRefs: string[];
}

export interface SearchEntry {
  id: string;
  /** Pre-folded haystack: whitespace-separated, one field per group. */
  hay: string;
}

export interface DocSearchIndex {
  entries: SearchEntry[];
  /** Schema names are global, so they fold into every entry that references them; this is the rest. */
  schemaHay: string;
  size: number;
}

function fold(value: unknown): string {
  return String(value ?? '').toLowerCase();
}

function group(values: readonly string[]): string {
  return values.map((v) => fold(v)).join(' ');
}

export function buildSearchIndex(operations: readonly SearchableOperation[], schemaNames: readonly string[] = []): DocSearchIndex {
  const entries: SearchEntry[] = operations.map((op) => ({
    id: op.id,
    hay: [
      fold(op.id),
      fold(op.path),
      fold(op.method),
      group(op.tags),
      fold(op.summary),
      fold(op.description),
      group(op.schemaRefs),
      group(op.registryRefs),
    ].join(' '),
  }));
  return { entries, schemaHay: group(schemaNames), size: entries.length };
}

/** Whitespace-separated terms, all of which must appear (AND). Empty query means "no filter". */
export function termsOf(query: string): string[] {
  return query.trim().toLowerCase().split(/\s+/).filter(Boolean);
}

/** Operation ids matching every term, in document order. */
export function searchOperations(index: DocSearchIndex, query: string): string[] {
  const terms = termsOf(query);
  if (!terms.length) return index.entries.map((entry) => entry.id);
  return index.entries.filter((entry) => terms.every((term) => entry.hay.includes(term))).map((entry) => entry.id);
}

/** A term that matches only the document-wide schema list still has to find something: the schema
 * names an operation references are already in its haystack, so this only answers "did the query
 * touch a schema the operations never mention?" — used to tell a reader the hit was a component. */
export function queryHitsSchemaOnly(index: DocSearchIndex, query: string): boolean {
  const terms = termsOf(query);
  if (!terms.length) return false;
  const hitsOperation = searchOperations(index, query).length > 0;
  const hitsSchema = terms.every((term) => index.schemaHay.includes(term));
  return hitsSchema && !hitsOperation;
}
