import type { JsonSchema } from '@/fui/schemaTypes';

/**
 * A trimmed copy of the shape S2's `sections.json` serves through
 * `GET /projects/{id}/blueprint/section-schemas/{section}`: self-contained Draft 2020-12, shared
 * types under `#/definitions/*`, `additionalProperties: false` on every object, and
 * enum-constrained scalars. Kept in a fixture rather than fetched so the editor's contract tests
 * run offline (UM-NFR-07).
 */
export const modelSchema = {

  $id: 'urn:damf:bp:1:test:project',
  title: 'project',
  type: 'object',
  additionalProperties: false,
  required: ['deliveryMode', 'projectType', 'defaultLanguage'],
  properties: {
    deliveryMode: { $ref: '#/definitions/deliveryMode' },
    projectType: { type: 'string', enum: ['GREENFIELD', 'BROWNFIELD'] },
    defaultLanguage: { $ref: '#/definitions/localizedText' },
    entities: { type: 'array', items: { $ref: '#/definitions/entity' } },
  },
  definitions: {
    deliveryMode: { type: 'string', enum: ['A', 'B'], description: '交付模式' },
    localizedText: {
      type: 'object',
      additionalProperties: false,
      required: ['value', 'language'],
      properties: { value: { type: 'string' }, language: { type: 'string' } },
    },
    entity: {
      type: 'object',
      additionalProperties: false,
      required: ['businessKey', 'displayName'],
      properties: {
        businessKey: { type: 'string', pattern: '^[a-z_]+\\.[a-z_]+$' },
        displayName: { type: 'string' },
      },
    },
  },
} as unknown as JsonSchema;

/** A document that satisfies `modelSchema`. */
export const validDoc = JSON.stringify(
  {
    deliveryMode: 'A',
    projectType: 'GREENFIELD',
    defaultLanguage: { value: '姓名', language: 'zh-CN' },
    entities: [{ businessKey: 'form_field.age', displayName: '年龄' }],
  },
  null,
  2,
);
