/*
 * UM-NAV-004: one term, four kinds of hit, and each hit has to know where it goes.
 *
 * The property worth pinning in a pure module is the routing table of a search result: a hit that
 * lists the right name but jumps nowhere is how a search box becomes decoration. The second thing
 * this holds is the scope honesty — stages and registry objects come from one project's reads, so a
 * term that matches nothing there must not silently look like "no stages exist anywhere".
 */
import { describe, expect, it } from 'vitest';
import { searchAll, type SearchSource } from '@/lib/search';

function source(over: Partial<SearchSource> = {}): SearchSource {
  return {
    projects: [{ id: 'prj-1', name: '免疫规划试点' }],
    stages: [{ id: 'st-1', stageType: 'DOMAIN', status: 'PASSED' }],
    environments: [{ environmentKey: 'dev', name: 'DEV', environmentClass: 'TEST' }],
    objects: [{ businessKey: 'dataelement.a', dhis2Type: 'dataelement' }],
    projectId: 'prj-1',
    ...over,
  };
}

describe('顶部搜索的四个去向（UM-NAV-004）', () => {
  it('asks nothing, gets nothing back — the panel then shows the recent list', () => {
    expect(searchAll('   ', source())).toEqual([]);
  });

  it('a term that matches every kind yields four groups, in the order the clause names them', () => {
    const groups = searchAll('e', source({
      projects: [{ id: 'prj-1', name: 'one' }],
      stages: [{ id: 'st-1', stageType: 'REQUIREMENTS' }],
      environments: [{ environmentKey: 'env-dev', environmentClass: 'DEV' }],
      objects: [{ businessKey: 'dataelement.x', dhis2Type: 'dataelement' }],
    }));
    expect(groups.map((g) => g.kind)).toEqual(['project', 'stage', 'environment', 'object']);
  });

  it('each group jumps to its own kind of address', () => {
    const groups = searchAll('免疫', source());
    expect(groups[0]?.items[0]?.to).toBe('/projects/prj-1');

    const stage = searchAll('domain', source()).find((g) => g.kind === 'stage');
    expect(stage?.items[0]?.to).toBe('/stages/st-1');

    const env = searchAll('dev', source()).find((g) => g.kind === 'environment');
    expect(env?.items[0]?.to).toBe('/environments/dev');

    const object = searchAll('dataelement.a', source()).find((g) => g.kind === 'object');
    // The object's address carries the project it was read from and the key to preselect: the graph
    // page is where a businessKey lives, and arriving there without it selected is a jump that
    // stops half way.
    expect(object?.items[0]?.to).toBe('/projects/prj-1/graph?businessKey=dataelement.a');
  });

  it('a key needing escaping still arrives as one key', () => {
    const groups = searchAll('b/c', source({ objects: [{ businessKey: 'a b/c', dhis2Type: 'dataelement' }], projectId: 'p 1' }))
      .find((g) => g.kind === 'object');
    expect(groups?.items[0]?.to).toBe('/projects/p%201/graph?businessKey=a%20b%2Fc');
  });

  it('drops a group with no hits instead of listing an empty heading', () => {
    const kinds = searchAll('nomatchatall', source()).map((g) => g.kind);
    expect(kinds).toEqual([]);
  });

  it('tolerates the nulls S1 answers with, and does not match on them', () => {
    const groups = searchAll('', source({ environments: [{ environmentKey: null, name: undefined, environmentClass: null }] }));
    expect(groups).toEqual([]);
    const hit = searchAll('dev', source({ environments: [{ environmentKey: 'dev', name: null, environmentClass: undefined }] }));
    expect(hit.find((g) => g.kind === 'environment')?.items).toHaveLength(1);
  });

  it('keeps a long group short: the panel is a jump list, not a result page', () => {
    const many = Array.from({ length: 40 }, (_, i) => ({ id: `prj-${i}`, name: `项目 ${i}` }));
    const group = searchAll('项目', source({ projects: many })).find((g) => g.kind === 'project');
    expect(group?.items).toHaveLength(6);
  });
});
