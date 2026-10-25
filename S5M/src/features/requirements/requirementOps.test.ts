import { describe, expect, it } from 'vitest';
import {
  applyEdit,
  buildRequirementPatch,
  buildStatusPatch,
  itemsFromEntries,
  requirementProblems,
  requirementRules,
  type RequirementItem,
} from './requirementOps';

/** The `t` the module is handed: the key alone, so assertions read as keys and not as sentences. */
const t = (key: string) => key;

const entry = (over: Partial<RequirementItem> = {}): RequirementItem => ({
  businessKey: 'req.b',
  statement: 'B',
  source: { note: 'uploaded doc' },
  ...over,
});

const read: RequirementItem[] = [
  { businessKey: 'req.a', statement: 'A', source: { artifactId: 'art-01M4E7M96B6RG1ENVFKBGM2RR7', location: 'line:8' }, actors: ['actor.midwife'] },
  entry(),
];

describe('itemsFromEntries — the read model is not the stored document', () => {
  it('drops S2’s locators, which are addresses rather than entry fields', () => {
    const [first] = itemsFromEntries([
      { businessKey: 'req.a', statement: 'A', source: { note: 'n' }, _section: 'requirements', _path: '/requirements/items/req.a' },
    ]);
    expect(first).toEqual({ businessKey: 'req.a', statement: 'A', source: { note: 'n' } });
    expect(JSON.stringify(first)).not.toContain('_path');
  });

  it('sorts by businessKey so a round trip re-orders nothing', () => {
    const keys = itemsFromEntries([
      { businessKey: 'req.z', statement: 'Z', source: { note: 'n' } },
      { businessKey: 'req.a', statement: 'A', source: { note: 'n' } },
    ]).map((i) => i.businessKey);
    expect(keys).toEqual(['req.a', 'req.z']);
  });
});

describe('buildRequirementPatch — append a create, set-replace an edit', () => {
  it('appends a new entry, so a concurrent addition cannot be overwritten', () => {
    const { operations, next } = buildRequirementPatch(read, { kind: 'create', item: entry({ businessKey: 'req.c', statement: 'C' }) });
    expect(operations.map((o) => o.op)).toEqual(['add']);
    expect(operations[0]).toMatchObject({ path: '/requirements/items/-' });
    expect((operations[0].value as RequirementItem).businessKey).toBe('req.c');
    // `next` is what the screen shows after the write, not what it sends
    expect(next.map((i) => i.businessKey)).toEqual(['req.a', 'req.b', 'req.c']);
  });

  it('keeps the fields the sheet never shows when an entry is edited', () => {
    const { operations } = buildRequirementPatch(read, {
      kind: 'update',
      businessKey: 'req.a',
      item: { businessKey: 'req.a', statement: 'A, rewritten', source: { note: 'operator clarified it' } },
    });
    const written = (operations[0].value as RequirementItem[]).find((i) => i.businessKey === 'req.a');
    expect(written?.statement).toBe('A, rewritten');
    // the parse pipeline’s provenance and the actors survive a statement edit
    expect(written?.actors).toEqual(['actor.midwife']);
    expect(written?.source).toMatchObject({ artifactId: 'art-01M4E7M96B6RG1ENVFKBGM2RR7', location: 'line:8' });
  });

  it('removes one entry and leaves the others untouched', () => {
    const { operations, removed } = buildRequirementPatch(read, { kind: 'delete', businessKey: 'req.a' });
    expect(removed?.businessKey).toBe('req.a');
    expect((operations[0].value as RequirementItem[]).map((i) => i.businessKey)).toEqual(['req.b']);
  });

  it(
    // The read model sorts by businessKey (`readmodel.py`) while the patch engine addresses array
    // members by index (`rfc6902.py::_array_index`), so no read tells this side where an entry sits.
    // A `test` on "index n-1" therefore asserts an order that is not observable: verified live, it
    // refused a legitimate deletion with 422 SCHEMA_ERROR / BP_PATCH_MALFORMED.
    'sends no test operation: the array position is not knowable from this side',
    () => {
      expect(buildRequirementPatch(read, { kind: 'delete', businessKey: 'req.b' }).operations.map((o) => o.op)).toEqual(['replace']);
      expect(buildRequirementPatch([], { kind: 'create', item: entry() }).operations.map((o) => o.op)).toEqual(['add']);
    },
  );
});

describe('buildStatusPatch — per-row and approve-all verdicts', () => {
  it('confirms exactly the named row and leaves the others alone', () => {
    const { operations, next, changed } = buildStatusPatch(read, ['req.a'], 'CONFIRMED');
    expect(changed).toBe(1);
    expect(operations).toEqual([{ op: 'replace', path: '/requirements/items', value: next }]);
    expect(next.find((i) => i.businessKey === 'req.a')?.status).toBe('CONFIRMED');
    expect(next.find((i) => i.businessKey === 'req.b')?.status).toBeUndefined();
  });

  it('changes nothing when every row already has the verdict', () => {
    const confirmed = read.map((i) => ({ ...i, status: 'CONFIRMED' }));
    const { operations, changed } = buildStatusPatch(confirmed, null, 'CONFIRMED');
    expect(changed).toBe(0);
    expect(operations).toEqual([]);
  });

  it('approve-all is one container replacement, not one operation per row', () => {
    const { operations, next, changed } = buildStatusPatch(read, null, 'CONFIRMED');
    expect(changed).toBe(2);
    expect(operations).toHaveLength(1);
    expect(next.every((i) => i.status === 'CONFIRMED')).toBe(true);
  });
});

describe('requirementRules — the served schema wins', () => {
  const served = {
    type: 'object',
    properties: {
      items: {
        type: 'array',
        items: {
          type: 'object',
          required: ['businessKey', 'statement', 'source'],
          properties: {
            businessKey: { $ref: '#/definitions/businessKey' },
            statement: { type: 'string', maxLength: 999 },
            priority: { type: 'string', enum: ['MUST', 'SHOULD'] },
            status: { type: 'string', enum: ['CANDIDATE'] },
            confidence: { $ref: '#/definitions/confidence' },
            openQuestions: { type: 'array', items: { properties: { severity: { enum: ['HIGH'] } } } },
            source: {
              anyOf: [{ required: ['note'] }, { required: ['artifactId'] }],
              properties: { note: { type: 'string', maxLength: 500 }, artifactId: { type: 'string' } },
            },
          },
        },
      },
    },
    definitions: {
      businessKey: { type: 'string', pattern: '^[a-z]+$', minLength: 3, maxLength: 9 },
      confidence: { type: 'number', minimum: 0.2, maximum: 0.8 },
    },
  } as never;

  it('reads limits, enums and the source alternatives out of the document S1 serves', () => {
    const rules = requirementRules(served);
    expect(rules.keyMin).toBe(3);
    expect(rules.keyMax).toBe(9);
    expect(rules.statementMax).toBe(999);
    expect(rules.noteMax).toBe(500);
    expect(rules.priorities).toEqual(['MUST', 'SHOULD']);
    expect(rules.statuses).toEqual(['CANDIDATE']);
    expect(rules.severities).toEqual(['HIGH']);
    expect(rules.confidenceMin).toBe(0.2);
    expect(rules.confidenceMax).toBe(0.8);
    expect(rules.sourceKeys).toEqual(['note', 'artifactId']);
    expect(rules.keyPattern.test('req.a')).toBe(false);
    expect(rules.keyPattern.test('abc')).toBe(true);
  });

  it('falls back to the console’s own vocabulary when the schema has not arrived', () => {
    const rules = requirementRules(undefined);
    expect(rules.priorities).toEqual(['MUST', 'SHOULD', 'COULD', 'WONT']);
    expect(rules.statuses).toEqual(['CANDIDATE', 'CONFIRMED', 'REJECTED']);
    expect(rules.keyPattern.test('req.a')).toBe(true);
    expect(rules.confidenceMax).toBe(1);
  });

  it('refuses to enforce a pattern the browser cannot compile', () => {
    const broken = {
      type: 'object',
      properties: { items: { type: 'array', items: { type: 'object', properties: { businessKey: { $ref: '#/definitions/businessKey' } } } } },
      definitions: { businessKey: { type: 'string', pattern: '([' } },
    };
    expect(() => requirementRules(broken as never)).not.toThrow();
    expect(requirementRules(broken as never).keyPattern.test('req.a')).toBe(true);
  });
});

describe('requirementProblems — S2’s rules applied before the click', () => {
  const rules = requirementRules();

  const keysOf = (item: RequirementItem, at: RequirementItem[] = read, editing: string | null = null) =>
    requirementProblems(item, at, rules, editing, t);

  it('names a key that breaks the served pattern', () => {
    expect(keysOf(entry({ businessKey: 'Req.Upper' }))).toContain('reqItems.keyShape');
  });

  it('catches a key that already belongs to another entry, and allows it on its own edit', () => {
    expect(keysOf(entry({ businessKey: 'req.a' }))).toContain('reqItems.keyTaken');
    expect(keysOf(entry({ businessKey: 'req.a' }), read, 'req.a')).not.toContain('reqItems.keyTaken');
  });

  it('requires a statement and a source that names where the entry came from', () => {
    expect(keysOf(entry({ statement: '  ' }))).toContain('reqItems.statementRequired');
    expect(keysOf(entry({ source: {} }))).toContain('reqItems.sourceRequired');
    expect(keysOf(entry({ source: { note: '' } }))).toContain('reqItems.sourceRequired');
  });

  it('rejects an out-of-range confidence, an unknown enum value and an empty open question', () => {
    expect(keysOf(entry({ confidence: 1.5 }))).toContain('reqItems.confidenceRange');
    expect(keysOf(entry({ priority: 'URGENT' }))).toContain('reqItems.priorityUnknown');
    expect(keysOf(entry({ openQuestions: [{ question: ' ', severity: 'HIGH' }] }))).toContain('reqItems.questionTextRequired');
    expect(keysOf(entry({ openQuestions: [{ question: 'who approves it?', severity: 'CRITICAL' }] }))).toContain('reqItems.questionSeverityUnknown');
  });

  it('accepts a well-formed new entry', () => {
    expect(keysOf(entry({ businessKey: 'req.delivery.call', statement: '随访电话' }))).toEqual([]);
  });

  it('applyEdit keeps the array sorted after an insert', () => {
    expect(applyEdit(read, { kind: 'create', item: entry({ businessKey: 'req.aa' }) }).map((i) => i.businessKey)).toEqual([
      'req.a',
      'req.aa',
      'req.b',
    ]);
  });
});
