import type { Completion, CompletionContext, CompletionResult, CompletionSource } from '@codemirror/autocomplete';
import type { JsonSchema } from '@/fui/schemaTypes';
import { cursorContext } from './offsetPointer';
import { enumCandidates, keyCandidates, schemaAtPointer } from './resolveSchema';

/**
 * Schema-driven completion for the JSON editor (UM-CAP-001: "基于 Schema 的键名与枚举值补全",
 * UM-AC-17: "输入键名首字母时出现补全建议").
 *
 * Two shapes of answer, picked by what the caret is on:
 *  - a key position → the property names the enclosing object allows *and has not been given yet*;
 *  - a value position → the enum literals that property permits (S2 sections lean on enums:
 *    `deliveryMode: ["A","B"]`, `projectType: ["GREENFIELD","BROWNFIELD"]`).
 *
 * `typeHint` is what the reader sees next to the candidate, so a `t()`-style translator is injected
 * rather than importing i18n into a library module.
 */

export interface CompletionLabels {
  keyDetail: string;
  requiredDetail: string;
  enumDetail: string;
}

function describeType(schema: JsonSchema | null): string {
  if (!schema) return '';
  const type = Array.isArray(schema.type) ? schema.type.join('|') : String(schema.type ?? '');
  if (type) return type;
  if (schema.$ref) return String(schema.$ref).split('/').pop() ?? '';
  return '';
}

export function schemaCompletion(schema: JsonSchema | null | undefined, labels: CompletionLabels): CompletionSource {
  return (ctx: CompletionContext): CompletionResult | null => {
    if (!schema) return null;
    const text = ctx.state.doc.toString();
    const pos = ctx.pos;

    // Require a typed prefix before popping a list, otherwise every quote keystroke opens it.
    const word = text.slice(Math.max(0, pos - 64), pos).match(/["']?([A-Za-z_$][\w$-]*)$/);
    if (!word) return null;
    const prefix = word[1];
    if (prefix.length < 1) return null;
    const from = pos - prefix.length;

    const c = cursorContext(text, pos);
    const options: Completion[] = [];

    if (c.kind === 'key') {
      const owner = schemaAtPointer(schema, c.pointer).schema;
      const present = new Set(c.presentKeys);
      for (const cand of keyCandidates(owner, present)) {
        const detail = cand.required ? labels.requiredDetail : describeType(cand.schema) || labels.keyDetail;
        options.push({
          label: cand.name,
          detail,
          info: typeof cand.schema?.description === 'string' ? cand.schema.description : undefined,
          type: 'property',
          apply: `${cand.name}": `,
        });
      }
    } else {
      // Value position. `cursorContext` reports the pointer of the value node itself (a property
      // value keeps its key, an array item keeps its index), so one lookup covers both.
      const owner = schemaAtPointer(schema, c.pointer).schema;
      for (const value of enumCandidates(owner)) {
        options.push({ label: value, detail: labels.enumDetail, type: 'enum', apply: value });
      }
      if (!options.length && owner) {
        if (owner.type === 'boolean') {
          options.push({ label: 'true', type: 'keyword', detail: labels.enumDetail }, { label: 'false', type: 'keyword', detail: labels.enumDetail });
        }
        if (owner.type === 'null' || (Array.isArray(owner.type) && owner.type.includes('null'))) {
          options.push({ label: 'null', type: 'keyword', detail: labels.enumDetail });
        }
      }
    }

    if (!options.length) return null;
    const ranked = options
      .filter((o) => o.label.toLowerCase().startsWith(prefix.toLowerCase()) || o.label.toLowerCase().includes(prefix.toLowerCase()))
      .sort((a, b) => a.label.localeCompare(b.label));
    if (!ranked.length) return null;
    return { from, to: pos, options: ranked, validFor: /^[A-Za-z_$][\w$-]*$/ };
  };
}
