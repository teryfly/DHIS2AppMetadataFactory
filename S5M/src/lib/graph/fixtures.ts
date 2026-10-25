import type { GraphEdgeView, GraphNodeView } from '@/api/types';

/**
 * Deterministic graph fixtures for the UM-CAP-002 benchmark.
 *
 * Seeded (mulberry32) so two runs are comparable and the recorded fps numbers can be reproduced
 * from `(n, seed)` alone. No JSON blobs are committed: a 5,000-node fixture file would be several
 * megabytes of noise that nobody reviews.
 *
 * Deliberate adversarial content, because the renderer has to survive what S1 can return:
 *  - a node with no `dhis2Type` (S1 leaves it null for Blueprint-only objects);
 *  - an edge whose far end is **not** in `nodes` (S1 promises it isn't, but a renderer that walks
 *    `pos.get(missing)` into `undefined` is the bug we are guarding);
 *  - a self-loop;
 *  - one oversized type, to exercise the >200 collapse-by-type default.
 */

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TYPES = ['dataelement', 'optionset', 'categoryoptioncombo', 'validationrule', 'programrule', 'dataset', 'predictor', 'indicator'];

export interface FixtureOptions {
  nodes: number;
  types?: number;
  /** Edges attached to each node on average. */
  edgesPerNode?: number;
  orphanPct?: number;
  /** Share of the nodes left without a UID, as the target instance would before a bind. */
  noUidPct?: number;
  seed?: number;
}

export interface GraphFixture {
  nodes: GraphNodeView[];
  edges: GraphEdgeView[];
  /** Business keys with no edge at all — S1 computes these on the whole build, not the page. */
  orphans: string[];
  /** Fields that point at a business key the Registry does not know (BP-TD-009). */
  unresolved: { fromBusinessKey: string; field: string; target: string }[];
}

export function makeGraphFixture(o: FixtureOptions): GraphFixture {
  const rnd = mulberry32(o.seed ?? 20261001);
  const typeCount = Math.max(1, Math.min(o.types ?? TYPES.length, TYPES.length));
  const nodes: GraphNodeView[] = [];
  for (let i = 0; i < o.nodes; i++) {
    // One oversized group, so a 5,000-node fixture still exercises the collapse threshold.
    const type = i < Math.min(300, o.nodes) ? TYPES[0] : TYPES[i % typeCount];
    const orphan = rnd() < (o.orphanPct ?? 0.06);
    const out = orphan ? 0 : 1 + Math.floor(rnd() * (o.edgesPerNode ?? 2));
    nodes.push({
      businessKey: `${type}.${String(i).padStart(5, '0')}`,
      dhis2Type: i % 97 === 0 ? null : type,
      uid: i % 5 === 0 ? `U${i}` : null,
      managed: rnd() > 0.3,
      inPayload: rnd() > 0.25,
      inDegree: orphan ? 0 : Math.floor(rnd() * 4),
      outDegree: out,
      disposition: ['CREATE', 'UPDATE', 'REFERENCE'][Math.floor(rnd() * 3)],
    });
  }

  const edges: GraphEdgeView[] = [];
  for (let i = 0; i < nodes.length; i++) {
    const count = Math.floor(rnd() * ((o.edgesPerNode ?? 2) + 1));
    for (let e = 0; e < count; e++) {
      const target = Math.floor(rnd() * nodes.length);
      edges.push({
        edgeKind: 'ref',
        field: ['dataElements', 'optionSet', 'categoryCombo'][Math.floor(rnd() * 3)],
        fromBusinessKey: nodes[i].businessKey,
        fromDhis2Type: nodes[i].dhis2Type,
        toBusinessKey: nodes[target].businessKey,
        toDhis2Type: nodes[target].dhis2Type,
      });
    }
  }

  // Adversarial additions: an edge to a node that is not in the payload, and a self-reference.
  const last = nodes[nodes.length - 1];
  if (last) {
    edges.push({
      edgeKind: 'ref',
      field: 'missing',
      fromBusinessKey: last.businessKey,
      toBusinessKey: 'ghost.does-not-exist',
    });
    edges.push({ edgeKind: 'ref', field: 'self', fromBusinessKey: last.businessKey, toBusinessKey: last.businessKey });
  }

  const orphans = nodes.filter((n) => n.inDegree === 0 && n.outDegree === 0).map((n) => String(n.businessKey));
  const unresolved = last
    ? [{ fromBusinessKey: String(last.businessKey), field: 'categoryCombo', target: 'ghost.does-not-exist' }]
    : [];

  return { nodes, edges, orphans, unresolved };
}
