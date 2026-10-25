import { load } from 'js-yaml';

/**
 * UM-CAP-003 document reading, part 1 of 3 (`parseDoc` -> `docModel` -> `resolve`).
 *
 * S2 writes the OpenAPI artifact as **YAML** (`OPENAPI_FILE = "api/openapi.yaml"`,
 * `OPENAPI_SPEC_VERSION = "3.1.0"` in S2 `factory_blueprint/compilers/openapi.py`), while S1 hands
 * the file over as plain text. The first version of this page called `JSON.parse` on it, so every
 * artifact rendered as "zero operations" (bug B2). JSON is still tried first because it is the
 * cheaper parse and a JSON document is a legal YAML document.
 *
 * Normalisation is deliberately shallow and structural, not semantic:
 *  - 3.1 union types (`type: ["string", "null"]`) and the 3.0 `nullable` flag both collapse to the
 *    same shape, so the viewer has one thing to render;
 *  - `content.<mediaType>.schema` is hoisted onto its parent (3.1's only way to attach a body
 *    schema) while the media type and any declared example are remembered;
 *  - a 3.0-style typed parameter (`{in, name, type}`) gains the `schema` object the viewer reads.
 * Nothing else is rewritten: an unknown keyword stays an unknown keyword, and an operation that
 * declares no error responses stays an operation with no error responses (see `docModel`).
 *
 * No DOM, no network, no throwing: bad input comes back as a typed failure the caller can render.
 */

export type ApiDocRoot = Record<string, unknown>;

/** Lower-case HTTP methods, in the order OpenAPI path items may carry them. */
export const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'] as const;
export type HttpMethod = (typeof HTTP_METHODS)[number];

/** Stable tokens; `src/i18n` owns the wording, the tests own the identity. */
export type DocWarning = 'specVersion' | 'specVersionUnknown' | 'noPaths' | 'noOperations';
export type DocFailure = 'empty' | 'unparseable' | 'shape';

export interface ApiDocParsed {
  ok: true;
  /** The normalised document, freshly produced by this call and safe for the caller to mutate. */
  root: ApiDocRoot;
  specVersion: string;
  format: 'json' | 'yaml';
  warnings: DocWarning[];
}

export interface ApiDocFailed {
  ok: false;
  reason: DocFailure;
  /** Parser diagnostics only (line/column text from js-yaml); never document content. */
  message: string;
}

export type ApiDocResult = ApiDocParsed | ApiDocFailed;

/** A media-type object hoisted onto its parent response/request body. */
export interface HoistedMedia {
  contentType: string;
  schema: unknown;
  /** `example` / `examples` as declared in the document — absent means absent. */
  declaredExample: unknown;
  hasDeclaredExample: boolean;
}

/** Keywords that identify a Parameter Object rather than a Schema Object. */
const PARAMETER_KEYS = new Set(['name', 'in', 'required', 'deprecated', 'allowEmptyValue', 'style', 'explode', 'allowReserved', 'example', 'examples', 'content', 'schema']);

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return isRecord(v) ? v : null;
}

function messageOf(err: unknown): string {
  if (err instanceof Error) return err.message.split('\n')[0].slice(0, 200);
  return String(err).slice(0, 200);
}

function failed(reason: DocFailure, message: string): ApiDocFailed {
  return { ok: false, reason, message };
}

/**
 * Parse and normalise. `raw` is whatever `useArtifactContent` handed back: a string today, and a
 * pre-parsed object if S1 ever stops serving this artifact as text.
 */
export function parseApiDocument(raw: unknown): ApiDocResult {
  if (raw === null || raw === undefined || raw === '') return failed('empty', '');

  let root: unknown = null;
  let format: 'json' | 'yaml' = 'yaml';
  let parseError = '';

  if (typeof raw === 'string') {
    const text = raw.trim();
    if (!text) return failed('empty', '');
    if (text.startsWith('{') || text.startsWith('[')) {
      try {
        root = JSON.parse(text);
        format = 'json';
      } catch (err) {
        // JSON is a subset of YAML: a document that failed `JSON.parse` may still be valid YAML,
        // and a hand-edited file is exactly the case that reaches here.
        parseError = messageOf(err);
        try {
          root = load(text);
          format = 'yaml';
          parseError = '';
        } catch (yamlErr) {
          return failed('unparseable', `${parseError} / ${messageOf(yamlErr)}`);
        }
      }
    } else {
      try {
        root = load(text);
      } catch (err) {
        return failed('unparseable', messageOf(err));
      }
    }
  } else {
    root = raw;
    format = 'json';
  }

  const doc = asRecord(root);
  if (!doc) return failed('shape', parseError || 'document is not an object');

  const declared = String(doc.openapi ?? doc.swagger ?? '');
  const warnings: DocWarning[] = [];
  if (!declared) warnings.push('specVersion');
  else if (declared.startsWith('2')) warnings.push('specVersionUnknown');

  walkSchemas(doc);
  normaliseOperations(doc);

  const paths = asRecord(doc.paths);
  if (!paths) warnings.push('noPaths');
  else if (!countOperations(paths)) warnings.push('noOperations');

  return { ok: true, root: doc, specVersion: declared, format, warnings };
}

function countOperations(paths: Record<string, unknown>): number {
  let n = 0;
  for (const item of Object.values(paths)) {
    const pi = asRecord(item);
    if (!pi) continue;
    for (const m of HTTP_METHODS) if (asRecord(pi[m])) n++;
  }
  return n;
}

/**
 * The first media type of a Content Object, with its schema and (if the document declares one) its
 * example. `application/json` wins when present, otherwise the first declared type: S2 emits one
 * JSON body per operation, and a viewer that picked a random sibling would be lying about the body.
 */
export function mediaOf(content: unknown): HoistedMedia | null {
  const bag = asRecord(content);
  if (!bag) return null;
  const keys = Object.keys(bag);
  if (!keys.length) return null;
  const contentType = keys.includes('application/json') ? 'application/json' : keys[0];
  const media = asRecord(bag[contentType]);
  if (!media) return null;
  const declared = declaredExampleOf(media);
  return {
    contentType,
    schema: media.schema ?? null,
    declaredExample: declared.value,
    hasDeclaredExample: declared.present,
  };
}

/** 3.0 `example`, 3.1 `examples` (array of values, or a named map of example objects). */
function declaredExampleOf(media: Record<string, unknown>): { present: boolean; value: unknown } {
  if ('example' in media && media.example !== undefined) return { present: true, value: media.example };
  const list = media.examples;
  if (Array.isArray(list) && list.length) return { present: true, value: list[0] };
  if (isRecord(list)) {
    const first = Object.keys(list)[0];
    if (first !== undefined) {
      const entry = asRecord(list[first]);
      if (entry && 'value' in entry) return { present: true, value: entry.value };
    }
  }
  return { present: false, value: null };
}

/**
 * One pass over every object in the document, folding the two dialects of "this value may be null"
 * and 3.1's schema-level `examples` array. Iterative on purpose: a `$ref`-heavy document nests
 * deeper than a call stack should be asked about, and this walk never needs to revisit a node.
 */
function walkSchemas(root: ApiDocRoot): void {
  const stack: unknown[] = [root];
  const seen = new Set<unknown>();
  while (stack.length) {
    const node = stack.pop();
    if (Array.isArray(node)) {
      for (const item of node) stack.push(item);
      continue;
    }
    if (!isRecord(node) || seen.has(node)) continue;
    seen.add(node);
    normaliseSchemaNode(node);
    for (const value of Object.values(node)) if (value && typeof value === 'object') stack.push(value);
  }
}

function normaliseSchemaNode(node: Record<string, unknown>): void {
  const type = node.type;
  if (Array.isArray(type)) {
    const types = type.filter((t): t is string => typeof t === 'string');
    const unique = [...new Set(types)];
    if (!unique.length) delete node.type;
    else if (unique.length === 1) node.type = unique[0];
    else node.type = unique;
  }

  if (node.nullable === true && typeof node.type === 'string' && node.type !== 'null') {
    node.type = [node.type, 'null'];
    delete node.nullable;
  } else if (node.nullable === true && Array.isArray(node.type) && !node.type.includes('null')) {
    (node.type as unknown[]).push('null');
    delete node.nullable;
  }

  if (node.example === undefined && Array.isArray(node.examples) && node.examples.length) {
    node.example = node.examples[0];
  }
}

function normaliseOperations(root: ApiDocRoot): void {
  const paths = asRecord(root.paths);
  if (!paths) return;
  for (const rawItem of Object.values(paths)) {
    const item = asRecord(rawItem);
    if (!item) continue;
    for (const method of HTTP_METHODS) {
      const op = asRecord(item[method]);
      if (!op) continue;
      normaliseParameters(op.parameters);
      const body = asRecord(op.requestBody);
      if (body && body.schema === undefined) hoistInto(body, mediaOf(body.content));
      const responses = asRecord(op.responses);
      if (responses) {
        for (const raw of Object.values(responses)) {
          const response = asRecord(raw);
          // A boolean response (`{"200": true}`) is legal 3.1 for "no body" — nothing to hoist.
          if (response && response.schema === undefined) hoistInto(response, mediaOf(response.content));
        }
      }
    }
  }
}

function hoistInto(target: Record<string, unknown>, media: HoistedMedia | null): void {
  if (!media) return;
  if (media.schema !== null && media.schema !== undefined) target.schema = media.schema;
  if (typeof media.contentType === 'string') target.contentType = media.contentType;
  if (media.hasDeclaredExample) {
    target.declaredExample = media.declaredExample;
    target.hasDeclaredExample = true;
  }
}

function normaliseParameters(raw: unknown): void {
  if (!Array.isArray(raw)) return;
  for (const entry of raw) {
    const parameter = asRecord(entry);
    if (!parameter || parameter.schema !== undefined) continue;
    const media = mediaOf(parameter.content);
    if (media && media.schema !== null) {
      parameter.schema = media.schema;
      continue;
    }
    // The 3.0 way: keywords sit on the Parameter Object itself. Rebuild them as a Schema Object so
    // the table and the tree have a single thing to read; the parameter keeps its own `name`/`in`.
    const schema: Record<string, unknown> = {};
    let taken = 0;
    for (const key of Object.keys(parameter)) {
      if (PARAMETER_KEYS.has(key)) continue;
      schema[key] = parameter[key];
      taken++;
    }
    if (taken) parameter.schema = schema;
  }
}
