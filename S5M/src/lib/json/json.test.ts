import { describe, expect, it } from 'vitest';
import { deref, enumCandidates, keyCandidates, propertySchema, schemaAtPointer } from './resolveSchema';
import { cursorContext, errorRange, formatEdits, fromPointer, presentKeys, syntaxIssues, toPointer, tryParse } from './offsetPointer';
import { diagnose } from './sectionValidator';
import { modelSchema, validDoc as doc } from './testSchema.fixture';
import { createBridge, mustConfirmLeavingJson, noteModelChange, noteTextEdit, shouldProjectModelIntoText } from './modelBridge';

// UM-CAP-001 / UM-AC-17. `doc` is a document that satisfies `modelSchema`, which mirrors the shape
// S2's sections.json uses: `additionalProperties: false`, shared `#/definitions/*`, enum scalars.

describe('resolveSchema · $ref', () => {
  it('resolves a definitions reference', () => {
    const r = propertySchema(modelSchema, modelSchema, 'deliveryMode');
    expect(r.schema?.enum).toEqual(['A', 'B']);
  });

  it('resolves a $ref-rooted property into its real shape', () => {
    const r = propertySchema(modelSchema, modelSchema, 'defaultLanguage');
    expect(r.schema?.type).toBe('object');
    expect(Object.keys(r.schema?.properties ?? {})).toContain('value');
  });

  it('marks a cycle instead of recursing forever', () => {
    const recursive = {
      $id: 'r',
      definitions: { node: { type: 'object', properties: { child: { $ref: '#/definitions/node' } } } },
      properties: { root: { $ref: '#/definitions/node' } },
    };
    const r = deref(recursive as never, { $ref: '#/definitions/node' } as never);
    expect(r.schema?.type).toBe('object');
  });

  it('reports an unresolvable pointer rather than crashing', () => {
    const r = deref(modelSchema, { $ref: '#/definitions/nope' } as never);
    expect(r.schema?.unresolvedRef).toBe('#/definitions/nope');
  });

  it('round-trips a pointer to the schema that validates it', () => {
    expect(schemaAtPointer(modelSchema, '/entities/0/businessKey').schema?.type).toBe('string');
  });

  it('offers only keys that are not already present', () => {
    const cand = keyCandidates(modelSchema, new Set(['deliveryMode']));
    expect(cand.map((c) => c.name)).not.toContain('deliveryMode');
    expect(cand.find((c) => c.name === 'projectType')?.required).toBe(true);
    expect(enumCandidates(modelSchema.properties?.projectType ?? null)).toEqual(['GREENFIELD', 'BROWNFIELD']);
  });
});

describe('offsetPointer · text↔path', () => {
  it('maps an additionalProperties error onto the offending key, not the whole object', () => {
    const broken = doc.replace('"deliveryMode": "A",', '"deliveryMode": "A",\n  "buzz": 1,');
    const at = broken.indexOf('"buzz"');
    const range = errorRange(broken, '', 'buzz');
    expect(range).not.toBeNull();
    expect(range!.from).toBe(at);
    expect(broken.slice(range!.from, range!.to)).toBe('"buzz"');
  });

  it('maps a nested pointer to its value range', () => {
    const range = errorRange(doc, '/entities/0/displayName');
    expect(range).not.toBeNull();
    expect(doc.slice(range!.from, range!.to)).toContain('年龄');
  });

  it('knows the caret is on a key and lists what the object already has', () => {
    const at = doc.indexOf('"entities"') + 3;
    const c = cursorContext(doc, at);
    expect(c.kind).toBe('key');
    if (c.kind === 'key') {
      expect(c.pointer).toBe('');
      expect(c.presentKeys).toContain('deliveryMode');
    }
  });

  it('exposes pointer helpers symmetrically', () => {
    expect(toPointer(['entities', 0, 'a/b'])).toBe('/entities/0/a~1b');
    expect(fromPointer('/a~1b/c')).toEqual(['a/b', 'c']);
  });

  it('still yields positions for a half-typed document', () => {
    const issues = syntaxIssues('{ "a": 1, ');
    expect(issues.length).toBeGreaterThan(0);
    expect(issues[0].message).toMatch(/PropertyName|Colon|Comma|Value|EndOfFile|Invalid/i);
    expect(tryParse('{ "a": 1, ')).toBeUndefined();
    expect(tryParse(doc)).toBeTruthy();
  });

  it('formats in one pass', () => {
    const edits = formatEdits('{"a":1,"b":[1,2]}', 2);
    expect(edits.length).toBeGreaterThan(0);
  });

  it('reports keys already present inside the object under the caret', () => {
    const at = doc.indexOf('"displayName"');
    const p = presentKeys(doc, at);
    expect(p.keys).toContain('businessKey');
  });
});

describe('sectionValidator · UM-AC-17', () => {
  it('flags a key that is not in the schema with its field path', () => {
    const broken = doc.replace('"deliveryMode": "A",', '"deliveryMode": "A", "buzz": 1,');
    const d = diagnose(modelSchema, broken);
    expect(d.blockedBySyntax).toBe(false);
    const hit = d.diagnostics.find((x) => x.keyword === 'additionalProperties');
    expect(hit).toBeTruthy();
    expect(hit!.params.additionalProperty).toBe('buzz');
    expect(hit!.path).toBe('/buzz');
    expect(broken.slice(hit!.from, hit!.to)).toBe('"buzz"');
  });

  it('flags a bad enum value', () => {
    const d = diagnose(modelSchema, doc.replace('"A"', '"Z"'));
    expect(d.diagnostics.some((x) => x.keyword === 'enum')).toBe(true);
  });

  it('names the missing required property', () => {
    const d = diagnose(modelSchema, '{ "entities": [] }');
    const missing = d.diagnostics.find((x) => x.keyword === 'required');
    expect(missing?.params.missingProperty).toBeTruthy();
  });

  it('stops at syntax and does not invent semantic errors', () => {
    const d = diagnose(modelSchema, '{ "a": ');
    expect(d.blockedBySyntax).toBe(true);
    expect(d.parsed).toBeUndefined();
    expect(d.diagnostics.every((x) => x.keyword === 'syntax')).toBe(true);
  });

  it('accepts a conforming document', () => {
    expect(diagnose(modelSchema, doc).diagnostics).toEqual([]);
  });
});

describe('modelBridge · 不丢失未知字段', () => {
  it('does not project an external model change into a dirty buffer', () => {
    const s = createBridge();
    noteTextEdit(s, 1);
    expect(shouldProjectModelIntoText(s, 'server')).toBe(false);
    expect(shouldProjectModelIntoText(s, 'editor')).toBe(false);
  });

  it('projects a server draft when nothing is being edited', () => {
    const s = createBridge();
    noteModelChange(s, 'server');
    expect(shouldProjectModelIntoText(s, 'server')).toBe(true);
  });

  it('blocks the tab switch that used to drop work', () => {
    expect(mustConfirmLeavingJson(true, true)).toBe(true);
    expect(mustConfirmLeavingJson(true, false)).toBe(false);
    expect(mustConfirmLeavingJson(false, true)).toBe(false);
  });
});
