import { describe, expect, it } from 'vitest';
import { qk } from './keys';

/*
 * TanStack Query v5 has no wildcard: `invalidateQueries` matches when the *filter* key is a prefix
 * of a query key (`partialMatchKey` in query-core). A key factory that appends a placeholder
 * instead of stopping therefore invalidates nothing, and the screen keeps showing what it read
 * before the write — which reads to the operator as "my save went missing".
 *
 * This is the rule the two-page pair (`blueprint/entries`) depends on, checked where it is written.
 */
describe('查询键的前缀必须真能命中（invalidateQueries 无通配符）', () => {
  it('the no-sections form is a strict prefix of the sections form', () => {
    const read = qk.blueprintEntries('prj-1', ['requirements']);
    const invalidate = qk.blueprintEntries('prj-1');
    expect(read.slice(0, invalidate.length)).toEqual([...invalidate]);
    expect(read.length).toBeGreaterThan(invalidate.length);
  });

  it('one invalidation covers every section combination the pages ask for', () => {
    const invalidate = qk.blueprintEntries('prj-1');
    for (const sections of [['requirements'], ['requirements', 'domain'], ['pages']]) {
      const read = qk.blueprintEntries('prj-1', sections);
      expect(read.slice(0, invalidate.length)).toEqual([...invalidate]);
    }
  });

  it('different sections are different queries, so a stale answer cannot be shown for another read', () => {
    expect(qk.blueprintEntries('prj-1', ['requirements'])).not.toEqual(qk.blueprintEntries('prj-1', ['pages']));
  });
});
