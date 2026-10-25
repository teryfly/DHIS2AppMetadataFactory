import type { ApiDocParsed, ApiDocRoot } from './parseDoc';
import { HTTP_METHODS } from './parseDoc';
import { collectRefs, createResolver, refLabel } from './resolve';
import type { SchemaResolver } from './resolve';
import { buildSearchIndex } from './search';
import type { DocSearchIndex } from './search';

/**
 * UM-CAP-003 flattening: one pass over the parsed document, then a stable read model.
 *
 * The shape matters because of what S2 actually writes (verified in
 * `factory_blueprint/compilers/openapi.py::_render`): tags come from `pageKey`, `operationId` from
 * `operationKey`, `x-registry-refs` sits on the operation, and **only `responses["200"]` exists** —
 * no 4xx, no 5xx, no `default`. A viewer that invented error responses would be documenting an API
 * that was never compiled, so this model records the absence (`errorResponses.length === 0`) and the
 * view says so.
 *
 * Schema trees are *not* built here. The resolver is handed over so a tree can be materialised when
 * a reader expands something (UM-TECH-002 keeps the cost where the interest is).
 */

export interface ApiParameter {
  name: string;
  location: string;
  required: boolean;
  description: string;
  schema: unknown;
  /** The component name when the parameter's schema is a `$ref`, else `''`. */
  ref: string;
  example: unknown;
  deprecated: boolean;
}

export interface ApiBody {
  required: boolean;
  description: string;
  contentType: string;
  schema: unknown;
  ref: string;
  hasDeclaredExample: boolean;
  declaredExample: unknown;
}

export interface ApiResponse {
  /** Status code as written, or `default`. */
  status: string;
  isDefault: boolean;
  /** True for 4xx/5xx/`default` — the "错误响应" the requirement names. */
  isError: boolean;
  description: string;
  contentType: string;
  schema: unknown;
  hasSchema: boolean;
  ref: string;
  hasDeclaredExample: boolean;
  declaredExample: unknown;
  headers: string[];
}

export interface ApiOperation {
  /** `operationId`; falls back to `METHOD path` when the document omits it. */
  id: string;
  path: string;
  method: string;
  methodLower: string;
  summary: string;
  description: string;
  tags: string[];
  deprecated: boolean;
  parameters: ApiParameter[];
  body: ApiBody | null;
  responses: ApiResponse[];
  /** The 4xx/5xx/`default` subset, kept separate so absence can be stated. */
  errorResponses: ApiResponse[];
  registryRefs: string[];
  intentNotes: string[];
  security: string[];
  /** `$ref`s reachable from this operation — the second half of the search haystack. */
  schemaRefs: string[];
  pointer: string;
}

export interface ApiTag {
  name: string;
  description: string;
  operations: ApiOperation[];
}

export interface ApiSchemaEntry {
  name: string;
  pointer: string;
  schema: unknown;
}

export interface ApiSecurityScheme {
  id: string;
  type: string;
  description: string;
  /** `header` / `cookie` / `query` for apiKey, `basic` / `bearer` for http. */
  detail: string;
}

export interface ApiDocModel {
  /** The normalised document itself — `exampleFromSchema` and every `$ref` walk start here. */
  root: ApiDocRoot;
  specVersion: string;
  format: 'json' | 'yaml';
  warnings: ApiDocParsed['warnings'];
  info: { title: string; version: string; description: string; extra: string[] };
  servers: { url: string; description: string }[];
  tags: ApiTag[];
  operations: ApiOperation[];
  operationsById: Map<string, ApiOperation>;
  schemas: ApiSchemaEntry[];
  schemasByName: Record<string, unknown>;
  securitySchemes: ApiSecurityScheme[];
  /** Operations that declare at least one error response — 0 for every document S2 emits today. */
  operationsWithErrors: number;
  counts: { operations: number; paths: number; tags: number; schemas: number; withBody: number; parameters: number };
  resolver: SchemaResolver;
  index: DocSearchIndex;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return isRecord(v) ? v : null;
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

function strList(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string');
  if (typeof v === 'string') return [v];
  return [];
}

function firstRef(schema: unknown): string {
  const direct = asRecord(schema);
  if (direct && typeof direct.$ref === 'string') return refLabel(direct.$ref);
  const refs = collectRefs(schema, 4);
  return refs.length ? refLabel(refs[0] ?? '') : '';
}

const UNNAMED = '(untagged)';

/** `default` last, then status codes ascending, then anything else in document order. */
function sortStatuses(statuses: string[]): string[] {
  const numeric = statuses.filter((s) => /^\d+$/.test(s)).sort((a, b) => Number(a) - Number(b));
  const rest = statuses.filter((s) => !/^\d+$/.test(s) && s !== 'default');
  return [...numeric, ...rest.sort(), ...(statuses.includes('default') ? ['default'] : [])];
}

function isErrorStatus(status: string, isDefault: boolean): boolean {
  if (isDefault) return true;
  return /^[45]/.test(status);
}

function readParameter(raw: unknown): ApiParameter | null {
  const parameter = asRecord(raw);
  if (!parameter) return null;
  return {
    name: str(parameter.name),
    location: str(parameter.in) || 'unknown',
    required: parameter.required === true,
    description: str(parameter.description),
    schema: parameter.schema ?? null,
    ref: firstRef(parameter.schema),
    example: parameter.example,
    deprecated: parameter.deprecated === true,
  };
}

function readBody(raw: unknown): ApiBody | null {
  const body = asRecord(raw);
  if (!body) return null;
  const hasSchema = body.schema !== undefined && body.schema !== null;
  const hasExample = body.hasDeclaredExample === true;
  return {
    required: body.required === true,
    description: str(body.description),
    contentType: str(body.contentType) || (body.content ? Object.keys(asRecord(body.content) ?? {})[0] ?? '' : ''),
    schema: hasSchema ? body.schema : null,
    ref: hasSchema ? firstRef(body.schema) : '',
    hasDeclaredExample: hasExample,
    declaredExample: hasExample ? body.declaredExample : null,
  };
}

function readResponses(raw: unknown): ApiResponse[] {
  const responses = asRecord(raw);
  if (!responses) return [];
  const statuses = sortStatuses(Object.keys(responses));
  const out: ApiResponse[] = [];
  for (const status of statuses) {
    const entry = asRecord(responses[status]);
    const isDefault = status.toLowerCase() === 'default';
    // `{"204": true}` is legal 3.1 and means "no content object supplied".
    const description = entry ? str(entry.description) : '';
    const hasSchema = !!entry && entry.schema !== undefined && entry.schema !== null;
    const hasExample = !!entry && entry.hasDeclaredExample === true;
    out.push({
      status,
      isDefault,
      isError: isErrorStatus(status, isDefault),
      description,
      contentType: entry ? str(entry.contentType) : '',
      schema: hasSchema ? entry.schema : null,
      hasSchema,
      ref: hasSchema ? firstRef(entry.schema) : '',
      hasDeclaredExample: hasExample,
      declaredExample: hasExample ? entry.declaredExample : null,
      headers: entry && isRecord(entry.headers) ? Object.keys(entry.headers as Record<string, unknown>) : [],
    });
  }
  return out;
}

function readSecurity(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const entry of raw) {
    const requirement = asRecord(entry);
    if (!requirement) continue;
    for (const scheme of Object.keys(requirement)) out.push(scheme);
  }
  return [...new Set(out)];
}

function readInfo(doc: ApiDocRoot): ApiDocModel['info'] {
  const info = asRecord(doc.info) ?? {};
  const extra: string[] = [];
  for (const [key, value] of Object.entries(info)) {
    if (!key.startsWith('x-')) continue;
    if (typeof value === 'string' || typeof value === 'number') extra.push(`${key}: ${String(value)}`);
  }
  return { title: str(info.title), version: str(info.version), description: str(info.description), extra };
}

function readServers(doc: ApiDocRoot): ApiDocModel['servers'] {
  if (!Array.isArray(doc.servers)) return [];
  return doc.servers.map((entry) => {
    const server = asRecord(entry) ?? {};
    return { url: str(server.url), description: str(server.description) };
  });
}

function readSecuritySchemes(doc: ApiDocRoot): ApiSecurityScheme[] {
  const components = asRecord(doc.components) ?? {};
  const schemes = asRecord(components.securitySchemes) ?? {};
  return Object.entries(schemes).map(([id, raw]) => {
    const scheme = asRecord(raw) ?? {};
    const type = str(scheme.type);
    let detail = '';
    if (type === 'apiKey') detail = `${str(scheme.in)}: ${str(scheme.name)}`;
    else if (type === 'http') detail = str(scheme.scheme);
    else if (type === 'oauth2' || type === 'openIdConnect') detail = str(scheme.flowOutputs ?? scheme.openIdConnectUrl);
    return { id, type, detail, description: str(scheme.description) };
  });
}

function schemaIndex(doc: ApiDocRoot): { map: Record<string, unknown>; entries: ApiSchemaEntry[] } {
  const components = asRecord(doc.components) ?? {};
  const named = asRecord(components.schemas) ?? asRecord(doc.definitions) ?? {};
  const entries: ApiSchemaEntry[] = [];
  const map: Record<string, unknown> = {};
  for (const [name, schema] of Object.entries(named)) {
    map[name] = schema;
    entries.push({ name, pointer: `#/components/schemas/${name}`, schema });
  }
  return { map, entries };
}

/**
 * Every component name an operation reaches, transitively. Direct `$ref`s alone would make the
 * full-text search fail on the documents S2 writes, where a response points at one page schema that
 * names the rest. Bounded by the component count and `seen`-guarded, so a cyclic schema graph ends.
 */
function reachableNames(direct: string[], index: Record<string, unknown>): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const queue: string[] = [];
  for (const name of direct) {
    if (name && !seen.has(name)) {
      seen.add(name);
      queue.push(name);
    }
  }
  while (queue.length && out.length < SCHEMA_WALK_LIMIT) {
    const name = queue.shift() as string;
    out.push(name);
    const schema = index[name];
    if (schema === undefined) continue;
    for (const ref of collectRefs(schema, 60)) {
      const label = refLabel(ref);
      if (label && !seen.has(label)) {
        seen.add(label);
        queue.push(label);
      }
    }
  }
  return out;
}

const SCHEMA_WALK_LIMIT = 200;

/** Flatten a parsed document once. Pure: the same root always yields the same model. */
export function buildDocModel(parsed: ApiDocParsed): ApiDocModel {
  const root = parsed.root;
  const paths = asRecord(root.paths) ?? {};
  const schemas = schemaIndex(root);
  const operations: ApiOperation[] = [];

  for (const [path, rawItem] of Object.entries(paths)) {
    const item = asRecord(rawItem);
    if (!item) continue;
    const shared = Array.isArray(item.parameters) ? item.parameters.map(readParameter).filter((p): p is ApiParameter => !!p) : [];
    for (const method of HTTP_METHODS) {
      const rawOp = asRecord(item[method]);
      if (!rawOp) continue;
      const parameters = [...shared, ...(Array.isArray(rawOp.parameters) ? rawOp.parameters.map(readParameter).filter((p): p is ApiParameter => !!p) : [])];
      const responses = readResponses(rawOp.responses);
      const id = str(rawOp.operationId) || `${method.toUpperCase()} ${path}`;
      const body = readBody(rawOp.requestBody);
      const tagList = strList(rawOp.tags);
      const schemaRefs = reachableNames([...new Set(collectRefs(rawOp).map(refLabel))], schemas.map);
      operations.push({
        id,
        path,
        method: method.toUpperCase(),
        methodLower: method,
        summary: str(rawOp.summary),
        description: str(rawOp.description),
        tags: tagList.length ? tagList : [UNNAMED],
        deprecated: rawOp.deprecated === true,
        parameters,
        body,
        responses,
        errorResponses: responses.filter((r) => r.isError),
        registryRefs: strList(rawOp['x-registry-refs']),
        intentNotes: strList(rawOp['x-intent-notes']),
        security: readSecurity(rawOp.security),
        schemaRefs,
        pointer: `#/paths/${encodePointerToken(path)}/${method}`,
      });
    }
  }

  // Declared tags first (they carry descriptions), then any tag an operation uses without declaring.
  const declared = Array.isArray(root.tags) ? root.tags : [];
  const tagMap = new Map<string, ApiTag>();
  for (const entry of declared) {
    const tag = asRecord(entry);
    if (!tag) continue;
    const name = str(tag.name);
    if (name) tagMap.set(name, { name, description: str(tag.description), operations: [] });
  }
  for (const operation of operations) {
    for (const name of operation.tags) {
      let tag = tagMap.get(name);
      if (!tag) {
        tag = { name, description: '', operations: [] };
        tagMap.set(name, tag);
      }
      tag.operations.push(operation);
    }
  }

  const resolver = createResolver(root);
  const base = {
    root,
    specVersion: parsed.specVersion,
    format: parsed.format,
    warnings: parsed.warnings,
    info: readInfo(root),
    servers: readServers(root),
    tags: [...tagMap.values()],
    operations,
    operationsById: new Map(operations.map((o) => [o.id, o])),
    schemas: schemas.entries,
    schemasByName: schemas.map,
    securitySchemes: readSecuritySchemes(root),
    operationsWithErrors: operations.filter((o) => o.errorResponses.length).length,
    counts: {
      operations: operations.length,
      paths: Object.keys(paths).length,
      tags: [...tagMap.values()].filter((t) => t.operations.length).length,
      schemas: schemas.entries.length,
      withBody: operations.filter((o) => o.body).length,
      parameters: operations.reduce((sum, o) => sum + o.parameters.length, 0),
    },
    resolver,
  };
  const model: ApiDocModel = { ...base, index: buildSearchIndex(base.operations, base.schemas.map((s) => s.name)) };
  return model;
}

function encodePointerToken(token: string): string {
  return token.replace(/~/g, '~0').replace(/\//g, '~1');
}

/** The placeholder tag name used when an operation declares no tag. */
export const UNTAGGED = UNNAMED;
