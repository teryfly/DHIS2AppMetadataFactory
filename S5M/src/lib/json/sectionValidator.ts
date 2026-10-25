import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import type { ErrorObject } from 'ajv';
import type { JsonSchema } from '@/fui/schemaTypes';
import { errorRange, syntaxIssues, tryParse } from './offsetPointer';

/**
 * Schema validation for the JSON editor (UM-CAP-001: "按 S2 分节 Schema 实时校验，错误定位到行列
 * 与字段路径并在编辑器内标注").
 *
 * Ajv is chosen because it compiles the schema to code — the only implementation fast enough to
 * re-check a 5,000-line document on a debounce — and because its errors carry both
 * `instancePath` (the JSON Pointer the reader is asked to see) and `params.additionalProperty`
 * (the offending key name, which is what makes an unknown-key squiggle land on the key).
 *
 * The module is UI-free: it returns message *keys* plus parameters, so the Chinese and English
 * strings live in the locale files like everything else (UM-AC-16).
 */

export interface Diagnostic {
  from: number;
  to: number;
  severity: 'error' | 'warning';
  /** JSON Pointer of the offending value, `''` for the document root. */
  path: string;
  keyword: string;
  messageKey: string;
  params: Record<string, string | number>;
  /** Raw Ajv text, kept only for the developer console and the fallback row. */
  raw: string;
}

/** Ajv's compiled validator: callable, with `errors` on the function object itself. */
type Compiled = {
  (data: unknown): boolean;
  errors: null | ErrorObject[];
};

interface CacheEntry {
  key: string;
  validate: Compiled;
}

// One Ajv instance per section document; schemas arrive from S1 and change only when the Blueprint
// version changes, so caching by $id is both correct and the whole cost model.
const instances = new Map<string, CacheEntry>();

function cacheKey(schema: JsonSchema): string {
  const id = typeof schema.$id === 'string' ? schema.$id : '';
  const version = typeof schema['x-schema-version'] === 'string' ? String(schema['x-schema-version']) : '';
  return `${id || 'anon'}|${version}|${schema.title ?? ''}|${Object.keys(schema.properties ?? {}).length}`;
}

function build(schema: JsonSchema): Compiled {
  const key = cacheKey(schema);
  const existing = instances.get(key);
  if (existing) return existing.validate;
  // Draft 2020-12: S2 section schemas declare `"$schema": ".../draft/2020-12/schema"`.
  // `strict: false` because S2 carries documentation keywords Ajv would otherwise reject outright.
  const ajv = new Ajv2020({ allErrors: true, verbose: true, strict: false, unicode: false, useDefaults: false, coerceTypes: false });
  addFormats(ajv);
  const validate = ajv.compile(schema as object) as unknown as Compiled;
  instances.set(key, { key, validate });
  return validate;
}

const MESSAGE_KEYS: Record<string, string> = {
  additionalProperties: 'jsonEditor.errUnknownKey',
  required: 'jsonEditor.errMissingRequired',
  type: 'jsonEditor.errType',
  enum: 'jsonEditor.errEnum',
  format: 'jsonEditor.errFormat',
  pattern: 'jsonEditor.errPattern',
  minimum: 'jsonEditor.errMinimum',
  maximum: 'jsonEditor.errMaximum',
  minLength: 'jsonEditor.errMinLength',
  maxLength: 'jsonEditor.errMaxLength',
  minItems: 'jsonEditor.errMinItems',
  const: 'jsonEditor.errConst',
};

function paramsOf(err: ErrorObject): Record<string, string | number> {
  const p = (err.params ?? {}) as Record<string, unknown>;
  const out: Record<string, string | number> = { path: err.instancePath || '/' };
  for (const key of ['additionalProperty', 'missingProperty', 'type', 'expected', 'format', 'pattern', 'limit', 'allowedValues'] as const) {
    const v = p[key];
    if (typeof v === 'string' || typeof v === 'number') out[key] = v;
    else if (Array.isArray(v)) out[key] = v.map((x) => String(x)).join(' / ');
  }
  return out;
}

export interface Diagnosis {
  diagnostics: Diagnostic[];
  /** Present only when the document parses; the editor uses it for the form sync. */
  parsed: unknown;
  /** True when a syntax error stopped semantic validation. */
  blockedBySyntax: boolean;
}

/**
 * Syntax first, schema second.
 *
 * Split because the keystroke path may not wait on Ajv: syntax errors are O(document) on the
 * parse the editor already has to do and are shown immediately, while schema errors are skipped
 * entirely when the document does not parse (an unparseable document cannot be validated, and
 * showing "must have required property" for a half-typed object is noise).
 */
export function diagnose(schema: JsonSchema | null | undefined, text: string): Diagnosis {
  const syntax = text.trim().length ? syntaxIssues(text) : [];
  if (!text.trim()) return { diagnostics: [], parsed: undefined, blockedBySyntax: false };

  if (syntax.length) {
    return {
      diagnostics: syntax.map((s) => ({
        from: s.range.from,
        to: s.range.to,
        severity: 'error' as const,
        path: '',
        keyword: 'syntax',
        messageKey: 'jsonEditor.errSyntax',
        params: { message: s.message },
        raw: s.message,
      })),
      parsed: undefined,
      blockedBySyntax: true,
    };
  }

  const parsed = tryParse(text);
  if (parsed === undefined || !schema) return { diagnostics: [], parsed, blockedBySyntax: parsed === undefined };

  const validate = build(schema);
  validate(parsed);
  const errors = validate.errors ?? [];

  const diagnostics: Diagnostic[] = [];
  for (const err of errors) {
    const name = (err.params as { additionalProperty?: string })?.additionalProperty;
    const range = errorRange(text, err.instancePath ?? '', name);
    if (!range) continue;
    diagnostics.push({
      from: range.from,
      to: range.to,
      severity: 'error',
      // The reader is asked for the *field* path: for a key the schema does not declare, the parent
      // pointer is not the offending location, the key is. So the stray name is appended.
      path: name ? `${err.instancePath || ''}/${name}` : err.instancePath || '/',
      keyword: err.keyword,
      messageKey: MESSAGE_KEYS[err.keyword] ?? 'jsonEditor.errGeneric',
      params: paramsOf(err),
      raw: err.message ?? err.keyword,
    });
  }
  return { diagnostics, parsed, blockedBySyntax: false };
}

/** Only for tests and the "clear when nothing is cached" path. */
export function resetValidatorCache(): void {
  instances.clear();
}
