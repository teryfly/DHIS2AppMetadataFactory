import { describe, expect, it } from 'vitest';
import { parseApiDocument } from './parseDoc';
import type { ApiDocParsed } from './parseDoc';
import { buildDocModel } from './docModel';
import type { ApiDocModel } from './docModel';
import { createResolver, collectRefs, pointerToNode, refLabel } from './resolve';
import type { SchemaNode } from './resolve';
import { exampleFromSchema, exampleAsText } from './exampleFromSchema';
import { buildSearchIndex, searchOperations, queryHitsSchemaOnly } from './search';

/**
 * UM-CAP-003 acceptance for the pure layer (`src/lib/openapi`).
 *
 * The fixture is written the way S2 writes: YAML, `3.1.0`, tags from `pageKey`, `operationId` from
 * `operationKey`, `x-registry-refs` on the operation, **only** `responses["200"]`, and body schemas
 * under `content.<mediaType>.schema` on one side and a bare `schema` on the other (both dialects
 * occur in documents this viewer is pointed at).
 */

const YAML_DOC = `
openapi: 3.1.0
info:
  title: ANC Blueprint API
  version: 1.4.0
  x-dhis2-version: 2.41.2
servers:
  - url: "{baseUrl}/api"
    description: the instance the app runs on
tags:
  - name: patients
    description: Patient register pages
paths:
  /trackedEntityInstances:
    get:
      operationId: listPatients
      summary: List patients for the register
      tags: [patients]
      x-registry-refs:
        - tei:Patient
      parameters:
        - name: page
          in: query
          schema: { type: integer, default: 1 }
        - name: ou
          in: query
          required: true
          schema: { type: string }
        - name: query
          in: query
          type: string
      responses:
        "200":
          description: a page of patients
          schema:
            $ref: "#/components/schemas/PatientPage"
  /trackedEntityInstances/{id}:
    put:
      operationId: updatePatient
      summary: Update one patient
      description: Writes the editable attributes only.
      tags: [patients]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/Patient"
            example:
              id: p-1
              status: ACTIVE
      responses:
        "200":
          description: the written patient
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Patient"
components:
  securitySchemes:
    apiToken:
      type: apiKey
      in: header
      name: Authorization
      description: personal access token
  schemas:
    Patient:
      type: object
      required: [id]
      properties:
        id: { type: string, description: the instance identifier }
        displayName: { type: ["string", "null"] }
        status: { type: string, enum: [INACTIVE, ACTIVE], default: ACTIVE }
        createdAt: { type: string, format: date-time }
        contacts:
          type: array
          items: { $ref: "#/components/schemas/Contact" }
        meta:
          allOf:
            - $ref: "#/components/schemas/NamedThing"
            - type: object
              properties: { extra: { type: string } }
        note:
          oneOf:
            - $ref: "#/components/schemas/TextNote"
            - type: "null"
        legacy: { $ref: "#/definitions/LegacyShape" }
    NamedThing:
      type: object
      properties:
        name: { type: string, minLength: 3 }
    Contact:
      type: object
      properties:
        phone: { type: string }
        parent: { $ref: "#/components/schemas/Contact" }
    TextNote:
      type: string
      example: first contact
    PatientPage:
      type: object
      properties:
        items:
          type: array
          items: { $ref: "#/components/schemas/Patient" }
        total: { type: integer, minimum: 0 }
definitions:
  LegacyShape:
    type: object
    properties:
      old: { type: boolean }
`;

function parsed(): ApiDocParsed {
  const result = parseApiDocument(YAML_DOC);
  if (!result.ok) throw new Error(`fixture failed to parse: ${result.reason} ${result.message}`);
  return result;
}

function model(): ApiDocModel {
  return buildDocModel(parsed());
}

function findNode(node: SchemaNode, name: string): SchemaNode | null {
  return findAll(node, name)[0] ?? null;
}

function findAll(node: SchemaNode, name: string): SchemaNode[] {
  const out: SchemaNode[] = [];
  const stack: SchemaNode[] = [node];
  while (stack.length) {
    const current = stack.pop() as SchemaNode;
    if (current.name === name) out.push(current);
    for (const child of current.children) stack.push(child);
  }
  return out;
}

describe('parseDoc · YAML and JSON', () => {
  it('reads the YAML artifact S2 writes (bug B2: the page used JSON.parse and rendered nothing)', () => {
    const result = parseApiDocument(YAML_DOC);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.format).toBe('yaml');
    expect(result.specVersion).toBe('3.1.0');
    expect(result.warnings).toEqual([]);
  });

  it('still reads JSON, since a JSON document is legal YAML', () => {
    const result = parseApiDocument('{"openapi":"3.1.0","info":{"title":"x"},"paths":{}}');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.format).toBe('json');
    expect(result.warnings).toContain('noOperations');
  });

  it('never throws on bad input, and names why', () => {
    expect(parseApiDocument('')).toMatchObject({ ok: false, reason: 'empty' });
    expect(parseApiDocument(null)).toMatchObject({ ok: false, reason: 'empty' });
    const broken = parseApiDocument('openapi: [unclosed');
    expect(broken.ok).toBe(false);
    if (!broken.ok) expect(broken.reason).toBe('unparseable');
    expect(parseApiDocument('- just\n- a\n- list')).toMatchObject({ ok: false, reason: 'shape' });
  });

  it('normalises a 3.1 union type and the 3.0 nullable flag to one shape', () => {
    const root = parsed().root;
    const schemas = (root.components as Record<string, Record<string, Record<string, unknown>>>).schemas ?? {};
    const patient = (schemas.Patient ?? {}).properties as Record<string, Record<string, unknown>>;
    expect(patient.displayName?.type).toEqual(['string', 'null']);
    expect(patient.id?.type).toBe('string');
    const nullable = parseApiDocument('openapi: 3.0.3\npaths: {}\ncomponents:\n  schemas:\n    A:\n      type: string\n      nullable: true\n');
    if (nullable.ok) {
      const a = ((nullable.root.components as Record<string, { A?: { type: unknown } }>).schemas ?? {}).A ?? { type: undefined };
      expect(a.type).toEqual(['string', 'null']);
      expect(a).not.toHaveProperty('nullable');
    } else {
      throw new Error('nullable fixture should parse');
    }
  });

  it('hoists content.<mediaType>.schema onto its parent and remembers the declared example', () => {
    const body = model().operationsById.get('updatePatient')?.body;
    expect(body?.contentType).toBe('application/json');
    expect(body?.ref).toBe('Patient');
    expect(body?.hasDeclaredExample).toBe(true);
    expect(body?.declaredExample).toEqual({ id: 'p-1', status: 'ACTIVE' });
  });

  it('gives a 3.0-style typed parameter a schema so the table has one thing to read', () => {
    const parameter = model().operationsById.get('listPatients')?.parameters.find((p) => p.name === 'query');
    expect(parameter?.schema).toEqual({ type: 'string' });
  });
});

describe('docModel · flatten once', () => {
  it('labels operations by operationId and tag by pageKey', () => {
    const m = model();
    expect(m.operations.map((o) => o.id)).toEqual(['listPatients', 'updatePatient']);
    expect(m.tags.map((t) => t.name)).toEqual(['patients']);
    expect(m.tags[0]?.description).toBe('Patient register pages');
    expect(m.tags[0]?.operations).toHaveLength(2);
  });

  it('carries x-registry-refs and the parameters the document declares', () => {
    const m = model();
    expect(m.operationsById.get('listPatients')?.registryRefs).toEqual(['tei:Patient']);
    expect(m.operationsById.get('listPatients')?.parameters.map((p) => p.name)).toEqual(['page', 'ou', 'query']);
    expect(m.counts.parameters).toBe(3);
  });

  it('shows error responses as absent, not invented', () => {
    const m = model();
    expect(m.operations.every((o) => o.responses.length === 1 && o.responses[0]?.status === '200')).toBe(true);
    expect(m.operationsWithErrors).toBe(0);
    expect(m.operationsById.get('listPatients')?.errorResponses).toEqual([]);
  });

  it('reads servers and security schemes as declared', () => {
    const m = model();
    expect(m.servers[0]?.url).toBe('{baseUrl}/api');
    expect(m.securitySchemes).toEqual([
      { id: 'apiToken', type: 'apiKey', detail: 'header: Authorization', description: 'personal access token' },
    ]);
  });

  it('indexes components by name, including the #/definitions fallback', () => {
    const m = model();
    expect(Object.keys(m.schemasByName)).toContain('PatientPage');
    expect(pointerToNode(m.resolver.root, '#/definitions/LegacyShape')).toBeTruthy();
    expect(m.resolver.resolveRef('#/definitions/LegacyShape').ok).toBe(true);
    expect(m.resolver.resolveRef('#/components/schemas/Nope').ok).toBe(false);
  });

  it('collects the component names an operation reaches transitively, for the search index', () => {
    const m = model();
    const refs = m.operationsById.get('listPatients')?.schemaRefs ?? [];
    expect([...refs].sort()).toEqual(['Contact', 'LegacyShape', 'NamedThing', 'Patient', 'PatientPage', 'TextNote']);
    expect(collectRefs({ $ref: '#/a', nested: { $ref: '#/b' } })).toEqual(['#/a', '#/b']);
    expect(refLabel('#/components/schemas/Patient')).toBe('Patient');
  });
});

describe('resolve · $ref, cycles and caps', () => {
  it('resolves a component reference into the tree, not just a name', () => {
    const m = model();
    const tree = m.resolver.subtree('body', { $ref: '#/components/schemas/Patient' }, { role: 'body' });
    expect(tree.ref).toBe('#/components/schemas/Patient');
    expect(tree.pointer).toBe('#/components/schemas/Patient');
    expect(tree.types).toEqual(['object']);
    const status = findNode(tree, 'status');
    expect(status?.enumValues).toEqual(['INACTIVE', 'ACTIVE']);
    expect(status?.defaultValue).toBe('ACTIVE');
    expect(status?.hasDefault).toBe(true);
    expect(findNode(tree, 'createdAt')?.format).toBe('date-time');
    expect(findNode(tree, 'id')?.required).toBe(true);
    expect(findNode(tree, 'displayName')?.types).toEqual(['string', 'null']);
    // `#/definitions/*` is the other pointer shape this repository's documents use.
    const legacy = findNode(tree, 'legacy');
    expect(legacy?.pointer).toBe('#/definitions/LegacyShape');
    expect((legacy?.children ?? []).map((c) => c.name)).toEqual(['old']);
  });

  it('marks a circular reference instead of recursing', () => {
    const m = model();
    const tree = m.resolver.subtree('body', { $ref: '#/components/schemas/Contact' }, { role: 'body' });
    const parent = findNode(tree, 'parent');
    expect(parent?.marker).toBe('recursive');
    expect(parent?.ref).toBe('#/components/schemas/Contact');
    expect(parent?.expandable).toBe(false);
    expect(parent?.loaded).toBe(true);
    expect(parent?.children).toEqual([]);
    expect(m.resolver.used()).toBeLessThan(m.resolver.maxNodes);
    // A second hop through a page schema is still a cycle, and still terminates.
    const viaPage = m.resolver.subtree('page', { $ref: '#/components/schemas/PatientPage' }, { role: 'body' });
    expect(findAll(viaPage, 'parent').some((n) => n.marker === 'recursive')).toBe(true);
  });

  it('applies 3.1 $ref siblings over what the reference points at', () => {
    const root = { components: { schemas: { Base: { type: 'string', description: 'from base' }, Use: { $ref: '#/components/schemas/Base', description: 'from sibling', enum: ['a', 'b'] } } } };
    const node = createResolver(root as never).subtree('use', root.components.schemas.Use);
    expect(node.types).toEqual(['string']);
    expect(node.description).toBe('from sibling');
    expect(node.enumValues).toEqual(['a', 'b']);
    expect(node.ref).toBe('#/components/schemas/Base');
  });

  it('merges allOf and names oneOf/anyOf variants', () => {
    const m = model();
    const tree = m.resolver.subtree('body', m.schemasByName.Patient, { role: 'body' });
    const meta = findNode(tree, 'meta');
    expect(meta?.composeRefs).toEqual(['NamedThing']);
    expect((meta?.children ?? []).map((c) => c.name).sort()).toEqual(['extra', 'name']);
    const note = findNode(tree, 'note');
    expect((note?.children ?? []).map((c) => `${c.role}:${c.name}`)).toEqual(['variant:TextNote', 'variant:null']);
  });

  it('honours the depth cap visibly', () => {
    let deep: Record<string, unknown> = { type: 'string' };
    for (let i = 0; i < 12; i++) deep = { type: 'object', properties: { child: deep } };
    const root = { paths: {}, components: { schemas: { Deep: deep } } };
    const tree = createResolver(root as never).subtree('Deep', deep);
    const markers = new Set<string>();
    const walk = (n: SchemaNode): void => {
      markers.add(n.marker);
      for (const c of n.children) walk(c);
    };
    walk(tree);
    expect(markers.has('')).toBe(true);
    expect(markers.has('depth')).toBe(true);
    expect([...markers]).toEqual(['', 'depth']);
    let depth = 0;
    let cursor: SchemaNode | null = tree;
    while (cursor) {
      depth = Math.max(depth, cursor.depth);
      cursor = cursor.children[0] ?? null;
    }
    expect(depth).toBe(8);
  });

  it('honours the node cap independently of depth', () => {
    const properties: Record<string, unknown> = {};
    for (let i = 0; i < 500; i++) properties[`k${i}`] = { type: 'string' };
    const tree = createResolver({ paths: {} } as never, { maxNodes: 40 }).subtree('wide', { type: 'object', properties });
    const capped = tree.children.filter((c) => c.marker === 'nodeCap');
    expect(capped.length).toBeGreaterThan(0);
    expect(tree.children.filter((c) => c.marker === 'depth')).toHaveLength(0);
    expect(tree.children).toHaveLength(500);
  });

  it('memoises one expansion per pointer, so a shared component is not paid for twice', () => {
    const m = model();
    const opts = { role: 'body' as const, pointer: '#/components/schemas/Patient' };
    const kids = m.resolver.childrenOf(m.resolver.node('body', m.schemasByName.Patient, opts));
    const before = m.resolver.used();
    const again = m.resolver.childrenOf(m.resolver.node('again', m.schemasByName.Patient, opts));
    expect(again).toBe(kids);
    // Only the second head node is new work; its eight children came from the memo.
    expect(m.resolver.used()).toBe(before + 1);
    expect(kids.length).toBeGreaterThan(0);
  });

  it('refuses to leave the document', () => {
    const m = model();
    // A foreign-file pointer is the reachable mistake: an absolute `scheme://` pointer is refused by
    // the same branch, but no URL literal may be written in `src` (isolation.test.ts), so it is not
    // spelled out here.
    expect(m.resolver.resolveRef('pet.yaml#/components/schemas/Pet').ok).toBe(false);
    // A bare component name is the tolerated shorthand, and it resolves inside the document only.
    expect(m.resolver.resolveRef('Patient').ok).toBe(true);
    expect(m.resolver.resolveRef('patient.yaml').ok).toBe(false);
    expect(pointerToNode(m.resolver.root, 'shared/pet.yaml')).toBeNull();
    expect(pointerToNode(m.resolver.root, '#/components/schemas/Patient')).toBeTruthy();
  });
});

describe('exampleFromSchema · deterministic derived examples', () => {
  it('is byte-identical across calls (no clock, no randomness)', () => {
    const m = model();
    const a = exampleAsText(exampleFromSchema(m.resolver.root, m.schemasByName.Patient));
    const b = exampleAsText(exampleFromSchema(m.resolver.root, m.schemasByName.Patient));
    expect(a).toBe(b);
  });

  it('prefers default, then enum[0], then a format-aware filler', () => {
    const m = model();
    const value = exampleFromSchema(m.resolver.root, m.schemasByName.Patient).value as Record<string, unknown>;
    expect(value.status).toBe('ACTIVE');
    expect(value.createdAt).toBe('2024-01-01T00:00:00Z');
    expect(value.id).toBe('sample');
    expect(value.displayName).toBe('sample');
  });

  it('falls to enum[0] when no default is declared', () => {
    const m = model();
    const value = exampleFromSchema(m.resolver.root, { type: 'object', properties: { s: { type: 'string', enum: ['INACTIVE', 'ACTIVE'] } } }).value as Record<string, unknown>;
    expect(value.s).toBe('INACTIVE');
  });

  it('emits exactly one array item and required keys first', () => {
    const m = model();
    const result = exampleFromSchema(m.resolver.root, m.schemasByName.Patient);
    const value = result.value as Record<string, unknown>;
    expect(Array.isArray(value.contacts)).toBe(true);
    expect(value.contacts).toHaveLength(1);
    expect(Object.keys(value).slice(0, 2)).toEqual(['id', 'displayName']);
    // The recursive member of `contacts[0]` cannot be expanded, and that is reported.
    expect(result.truncated).toBe(true);
    expect(result.stopped).toBe('recursive');
    expect((value.contacts as Record<string, unknown>[])[0]?.parent).toBeUndefined();
  });

  it('honours a declared example and clamps numbers to minimum', () => {
    const m = model();
    expect(exampleFromSchema(m.resolver.root, m.schemasByName.TextNote).value).toBe('first contact');
    const page = exampleFromSchema(m.resolver.root, m.schemasByName.PatientPage).value as Record<string, unknown>;
    expect(page.total).toBe(1);
    expect(page.items).toHaveLength(1);
  });

  it('never fabricates past the caps and says where it stopped', () => {
    let deep: Record<string, unknown> = { type: 'string' };
    for (let i = 0; i < 12; i++) deep = { type: 'object', properties: { child: deep } };
    const result = exampleFromSchema({ paths: {} } as never, deep);
    expect(result.truncated).toBe(true);
    expect(result.stopped).toBe('depth');
  });
});

describe('search · full text over the visible document', () => {
  it('finds an operation by its summary text, case-folded', () => {
    const m = model();
    expect(searchOperations(m.index, 'register')).toEqual(['listPatients']);
    expect(searchOperations(m.index, 'REGISTER')).toEqual(['listPatients']);
    expect(searchOperations(m.index, 'update one')).toEqual(['updatePatient']);
  });

  it('finds by operationId, path, tag, schema name and registry ref', () => {
    const m = model();
    expect(searchOperations(m.index, 'listPatients')).toEqual(['listPatients']);
    expect(searchOperations(m.index, 'trackedEntityInstances')).toEqual(['listPatients', 'updatePatient']);
    expect(searchOperations(m.index, 'patients')).toEqual(['listPatients', 'updatePatient']);
    expect(searchOperations(m.index, 'patientpage')).toEqual(['listPatients']);
    expect(searchOperations(m.index, 'tei:patient')).toEqual(['listPatients']);
  });

  it('combines terms with AND and answers nothing for a term that is not there', () => {
    const m = model();
    expect(searchOperations(m.index, 'patient register')).toEqual(['listPatients']);
    expect(searchOperations(m.index, 'patient nonexistent')).toEqual([]);
    expect(searchOperations(m.index, '   ')).toEqual(['listPatients', 'updatePatient']);
  });

  it('distinguishes a component-only hit from an operation hit', () => {
    const m = model();
    expect(queryHitsSchemaOnly(m.index, 'naname')).toBe(false);
    const index = buildSearchIndex([{ id: 'a', path: '/a', method: 'GET', summary: '', description: '', tags: [], schemaRefs: [], registryRefs: [] }], ['ghostshape']);
    expect(queryHitsSchemaOnly(index, 'ghostshape')).toBe(true);
  });
});
