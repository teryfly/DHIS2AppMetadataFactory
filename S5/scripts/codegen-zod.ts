/*
 * UI-APP-007: data validation must use Zod models generated from the SAME OpenAPI the
 * client is generated from. This script reads the vendored S1 contract
 * (contract/openapi.json — OpenAPI 3.1, whose schemas are native JSON-Schema draft 2020-12)
 * and emits src/api/generated/zod.ts: one Zod schema per component schema, plus a runtime
 * lookup map keyed by the S1 schema name.
 *
 * It is deliberately dependency-light (no babel): it walks the JSON-Schema nodes and
 * prints Zod expressions directly. Cross-schema $refs are wrapped in z.lazy() so
 * declaration order and cycles never matter.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const contractPath = path.resolve(here, '../contract/openapi.json');
const outPath = path.resolve(here, '../src/api/generated/zod.ts');

type Node = Record<string, any>;

const doc = JSON.parse(readFileSync(contractPath, 'utf8')) as {
  components: { schemas: Record<string, Node> };
};
const schemas = doc.components.schemas;

const definedNames = new Set(Object.keys(schemas));
// Deterministic order → stable diffs.
const names = Object.keys(schemas).sort();

function refName(ref: string): string {
  // "#/components/schemas/Foo" → "Foo"
  return ref.split('/').pop() ?? '';
}

function isPlainObject(v: unknown): v is Node {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Render a JSON value used as an enum/const member into a Zod literal expression. */
function lit(v: unknown): string {
  return JSON.stringify(v);
}

/** Convert one JSON-Schema node into a Zod expression string. */
function toZod(node: Node): string {
  if (node === true) return 'z.unknown()';
  if (node === false) return 'z.never()';
  if (!isPlainObject(node)) return 'z.unknown()';

  // Boolean algebra
  if (Array.isArray(node.allOf)) {
    const parts = node.allOf.filter(isPlainObject);
    if (parts.length === 0) return 'z.unknown()';
    return parts.map((p) => `(${toZod(p)})`).join('.and(') + ')'.repeat(parts.length - 1);
  }
  for (const key of ['oneOf', 'anyOf'] as const) {
    if (Array.isArray(node[key])) {
      const branches: Node[] = node[key].filter(isPlainObject);
      const nullBranch = branches.some((b) => b.type === 'null');
      const valueBranches = branches.filter((b) => b.type !== 'null');
      let expr: string;
      if (valueBranches.length === 0) {
        expr = 'z.null()';
      } else if (valueBranches.length === 1) {
        expr = toZod(valueBranches[0]!);
      } else {
        expr = `z.union([${valueBranches.map((b) => toZod(b)).join(', ')}])`;
      }
      // `anyOf: [X, {type:null}]` (3.1 nullable) → nullable + optional.
      if (nullBranch) expr = `${expr}.nullish()`;
      return expr;
    }
  }

  if (typeof node.$ref === 'string') {
    const name = refName(node.$ref);
    return definedNames.has(name) ? `z.lazy(() => ${safeId(name)})` : 'z.unknown()';
  }

  if (Array.isArray(node.enum)) {
    const vals = node.enum.filter((v) => v !== null);
    if (vals.length > 0 && vals.every((v) => typeof v === 'string')) {
      return `z.enum([${vals.map((v) => lit(v)).join(', ')}] as const)`;
    }
    if (vals.length > 0) {
      return `z.union([${vals.map((v) => `z.literal(${lit(v)})`).join(', ')}])`;
    }
    // enum containing only null
    return 'z.null()';
  }

  if ('const' in node) return `z.literal(${lit(node.const)})`;

  // Normalise type that may be an array (e.g. ["string","null"])
  const types: string[] = (Array.isArray(node.type) ? node.type : [node.type]).filter(Boolean);
  const nullable = node.nullable === true || types.includes('null');
  const primary = types.find((t) => t !== 'null');

  let expr: string;
  switch (primary) {
    case 'string':
      expr = 'z.string()';
      break;
    case 'integer':
      expr = 'z.number().int()';
      break;
    case 'number':
      expr = 'z.number()';
      break;
    case 'boolean':
      expr = 'z.boolean()';
      break;
    case 'null':
      expr = 'z.null()';
      break;
    case 'array': {
      const items = isPlainObject(node.items) || node.items === true || node.items === false
        ? toZod(node.items as Node)
        : 'z.unknown()';
      expr = `z.array(${items})`;
      if (typeof node.minItems === 'number') expr += `.min(${node.minItems})`;
      if (typeof node.maxItems === 'number') expr += `.max(${node.maxItems})`;
      break;
    }
    case 'object':
    default: {
      // No explicit type but has properties → treat as object; otherwise open/unknown.
      const props: Node = isPlainObject(node.properties) ? node.properties : {};
      const required: string[] = Array.isArray(node.required) ? node.required : [];
      const reqSet = new Set(required);
      const propEntries = Object.entries(props);
      if (propEntries.length === 0) {
        // object with additionalProperties only, or unconstrained
        if (isPlainObject(node.additionalProperties) || node.additionalProperties === true) {
          expr = `z.record(z.string(), ${toZod(node.additionalProperties as Node)})`;
        } else {
          expr = 'z.record(z.string(), z.unknown())';
        }
        break;
      }
      const fields = propEntries.map(([k, v]) => {
        let fe = toZod(v as Node);
        if (!reqSet.has(k) && !fe.endsWith('.nullish()') && !fe.endsWith('.optional()')) {
          fe = `${fe}.optional()`;
        }
        return `${keyId(k)}: ${fe}`;
      });
      // Use a permissive z.object (not strict) so S1 additions never break parsing.
      expr = `z.object({\n      ${fields.join(',\n      ')},\n    })`;
      break;
    }
  }

  if (nullable && !expr.endsWith('.nullish()') && !expr.endsWith('.nullable()')) {
    expr = `${expr}.nullable()`;
  }
  return expr;
}

function safeId(name: string): string {
  // Component names can contain characters invalid in identifiers (e.g. $id). They are
  // always Pascal/snake here, but guard anyway.
  return /^[A-Za-z_$][\w$]*$/.test(name) ? name : identFor(name);
}
function identFor(name: string): string {
  return 'S_' + name.replace(/[^A-Za-z0-9_]/g, '_');
}
function keyId(k: string): string {
  return /^[A-Za-z_$][\w$]*$/.test(k) ? k : JSON.stringify(k);
}

const lines: string[] = [];
lines.push('/* eslint-disable */');
lines.push('// AUTO-GENERATED from contract/openapi.json by scripts/codegen-zod.ts.');
lines.push('// Do not edit by hand — regenerate with `npm run codegen:zod`.');
lines.push("import { z } from 'zod';");
lines.push('');

for (const name of names) {
  const id = safeId(name);
  lines.push(`export const ${id} = ${toZod(schemas[name]!)};`);
  lines.push('');
}

lines.push('/** Runtime lookup: S1 component schema name → its generated Zod schema. */');
lines.push('export const zodSchemas: Record<string, z.ZodTypeAny> = {');
for (const name of names) {
  lines.push(`  ${JSON.stringify(name)}: ${safeId(name)},`);
}
lines.push('};');
lines.push('');

writeFileSync(outPath, lines.join('\n'), 'utf8');
console.log(`zod.ts: generated ${names.length} schemas → ${path.relative(process.cwd(), outPath)}`);
