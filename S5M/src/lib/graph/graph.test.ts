import { describe, expect, it } from 'vitest';
import { buildScene, COLLAPSE_THRESHOLD, NodeFlag } from './scene';
import { makeGraphFixture } from './fixtures';

// UM-CAP-002: ">200 个节点时默认按对象类型折叠，点选可逐组展开". The fold state is scene data, not
// renderer state, so both arms inherit it — and it is scene data that can be tested without a DOM.
describe('UM-CAP-002 · 按类型折叠的场景语义', () => {
  const fx = makeGraphFixture({ nodes: 250, edgesPerNode: 2 });
  // Column labels come from the scene, not from the raw payload: nodes whose `dhis2Type` is missing
  // are gathered under one localised label, and that label is what `expandedTypes` has to name.
  const types = buildScene(fx.nodes, fx.edges).types;

  it('folds by type above the threshold, and folds every node', () => {
    const s = buildScene(fx.nodes, fx.edges);
    expect(COLLAPSE_THRESHOLD).toBe(200);
    expect(s.groups?.length).toBe(types.length);
    expect(s.loose).toEqual([]);
    expect(s.flags.every((f) => (f & NodeFlag.Grouped) !== 0)).toBe(true);
  });

  it('expanding one type unfolds exactly that type', () => {
    const one = types[types.length - 1];
    const s0 = buildScene(fx.nodes, fx.edges);
    const size = s0.groups!.find((g) => g.type === one)!.count;
    const s = buildScene(fx.nodes, fx.edges, { expandedTypes: new Set([one]) });
    expect(s.groups?.length).toBe(types.length - 1);
    expect(s.groups!.some((g) => g.type === one)).toBe(false);
    expect(s.loose.length).toBe(size);
  });

  it('expanding every type leaves no super-node behind', () => {
    const s = buildScene(fx.nodes, fx.edges, { expandedTypes: new Set(types) });
    expect(s.groups).toBeNull();
    expect(s.loose.length).toBe(fx.nodes.length);
  });

  it('leaves a small graph unfolded', () => {
    const small = makeGraphFixture({ nodes: 120, edgesPerNode: 2 });
    const s = buildScene(small.nodes, small.edges);
    expect(s.groups).toBeNull();
    expect(s.loose.length).toBe(120);
  });
});

// The flags come from the answer S1 gives about the whole build, not from anything the browser can
// see in the returned slice (BP-TD-009): `orphans` and `unresolvedRegistryRefs` are separate lists,
// and a node without a UID is a different fact again.
describe('UM-CAP-002 · 节点标注来自 S1 的整图判定', () => {
  const nodes = [
    { businessKey: 'a', dhis2Type: 'dataElement', uid: 'u1', managed: true, inPayload: true, inDegree: 1, outDegree: 1, disposition: 'CREATE' },
    { businessKey: 'b', dhis2Type: 'dataElement', uid: null, managed: false, inPayload: false, inDegree: 0, outDegree: 0, disposition: 'REFERENCE' },
  ] as unknown as Parameters<typeof buildScene>[0];
  const edges = [{ edgeKind: 'ref', field: 'f', fromBusinessKey: 'a', toBusinessKey: 'b' }] as unknown as Parameters<typeof buildScene>[1];
  const flagsOf = (scene: ReturnType<typeof buildScene>, key: string) =>
    scene.flags[scene.keys.indexOf(key)];

  it('marks the orphan S1 named, not the one the filter happens to have cut', () => {
    const s = buildScene(nodes, edges, { orphans: new Set(['b']), unresolved: new Set(['a']) });
    expect(flagsOf(s, 'b') & NodeFlag.Orphan).not.toBe(0);
    expect(flagsOf(s, 'a') & NodeFlag.Orphan).toBe(0);
  });

  it('marks an unresolved reference separately from a missing UID', () => {
    const s = buildScene(nodes, edges, { orphans: new Set(['b']), unresolved: new Set(['a']) });
    expect(flagsOf(s, 'a') & NodeFlag.Unresolved).not.toBe(0);
    expect(flagsOf(s, 'b') & NodeFlag.NoUid).not.toBe(0);
    expect(flagsOf(s, 'a') & NodeFlag.NoUid).toBe(0);
  });

  it('draws a stub for an edge whose far end is absent from the payload', () => {
    const cut = buildScene(nodes, [{ edgeKind: 'ref', field: 'f', fromBusinessKey: 'a', toBusinessKey: 'ghost' }] as unknown as typeof edges);
    expect(flagsOf(cut, 'a') & NodeFlag.EdgeCut).not.toBe(0);
  });
});
