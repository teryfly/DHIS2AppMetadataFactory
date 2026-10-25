import { t as tr } from '@/lib/labels';
import type { GraphEdgeView, GraphNodeView } from '@/api/types';

/**
 * Graph scene model for UM-CAP-002.
 *
 * Everything the frame loop touches lives **outside Vue reactivity**: positions in a Float32Array
 * and adjacency in CSR Int32Arrays. A reactive array of 5,000 node objects would re-collect on
 * every pointer move, and the proxy cost alone is enough to miss 30 fps on a throttled phone.
 *
 * Layout is columns-by-type, which is what a reviewer of a DHIS2 model actually reads ("all my
 * data elements, then all my option sets"), and it is O(n) to compute — a force simulation would
 * cost a second per rebuild and would move nodes under the reader's cursor.
 */

export const COLLAPSE_THRESHOLD = 200;
const COL_W = 220;
const ROW_H = 40;
const PAD = 40;
const NODE_R = 10;

export const enum NodeFlag {
  /** No edge touches it, on the complete build — S1's `orphans`, not a property of the filter. */
  Orphan = 1,
  /** Registry entry without a UID yet: nothing has been bound in the target instance. */
  NoUid = 2,
  Managed = 4,
  /** The far end of one of its edges is missing from the payload — a data-integrity fallback. */
  EdgeCut = 8,
  /** Its type is folded into a super-node, so it must not be drawn or hit-tested itself. */
  Grouped = 16,
  /** A field of this object points at a business key the Registry does not know (BP-TD-009). */
  Unresolved = 32,
}

export interface SceneEdge {
  from: number;
  to: number;
  /** Index into `fields`; -1 when the edge is between two collapsed groups. */
  field: number;
}

export interface GraphScene {
  count: number;
  /** Float32Array of x,y pairs in node order. */
  pos: Float32Array;
  flags: Uint8Array;
  typeIdx: Uint16Array;
  keys: string[];
  types: string[];
  fields: string[];
  edges: SceneEdge[];
  /** CSR: outStart[i]..outStart[i+1] indexes into outEdge. */
  outStart: Int32Array;
  outEdge: Int32Array;
  inStart: Int32Array;
  inEdge: Int32Array;
  /** Folded types: one super-node each. Null when nothing is folded. */
  groups: { type: string; count: number; x: number; y: number; idx: number }[] | null;
  /** Node indices that are drawn individually — i.e. every node whose type is not folded. */
  loose: number[];
  width: number;
  height: number;
}

function buildEdges(edges: GraphEdgeView[], indexOf: Map<string, number>): { sceneEdges: SceneEdge[]; fields: string[]; cut: Set<number> } {
  const fieldIndex = new Map<string, number>();
  const out: SceneEdge[] = [];
  const cut = new Set<number>();
  for (const e of edges) {
    const from = indexOf.get(String(e.fromBusinessKey));
    const to = indexOf.get(String(e.toBusinessKey));
    if (from === undefined) continue;
    if (to === undefined) {
      // S1 guarantees both ends are present; if a payload ever breaks that, mark the source and
      // draw a stub rather than indexing an undefined node. This is not the same fact as an
      // unresolved Registry reference, which is what `orphans`/`unresolved` carry in.
      cut.add(from);
      continue;
    }
    const field = String(e.field ?? '');
    if (!fieldIndex.has(field)) fieldIndex.set(field, fieldIndex.size);
    out.push({ from, to, field: fieldIndex.get(field)! });
  }
  return { sceneEdges: out, fields: [...fieldIndex.keys()], cut };
}

/** Columns by DHIS2 type; the order is stable so a re-layout does not make the reader re-orient. */
function columnsByType(nodes: GraphNodeView[]): Map<string, number[]> {
  const by = new Map<string, number[]>();
  nodes.forEach((n, i) => {
    const label = String(n.dhis2Type ?? '') || tr('graph.untyped');
    if (!by.has(label)) by.set(label, []);
    by.get(label)!.push(i);
  });
  return by;
}

export interface SceneOptions {
  /** Types the reader has expanded out of the collapsed view. */
  expandedTypes?: ReadonlySet<string>;
  /** `orphans` and `unresolvedRegistryRefs` as sent by S1; both describe the whole build. */
  orphans?: ReadonlySet<string>;
  unresolved?: ReadonlySet<string>;
  collapseThreshold?: number;
}

export function buildScene(nodes: GraphNodeView[], edges: GraphEdgeView[], opts: SceneOptions = {}): GraphScene {
  const threshold = opts.collapseThreshold ?? COLLAPSE_THRESHOLD;
  const indexOf = new Map<string, number>();
  nodes.forEach((n, i) => indexOf.set(String(n.businessKey), i));

  const { sceneEdges, fields, cut } = buildEdges(edges, indexOf);
  const count = nodes.length;
  const orphans = opts.orphans ?? new Set<string>();
  const unresolved = opts.unresolved ?? new Set<string>();
  const flags = new Uint8Array(count);
  nodes.forEach((n, i) => {
    const key = String(n.businessKey);
    let f = 0;
    if (orphans.has(key)) f |= NodeFlag.Orphan;
    if (!n.uid) f |= NodeFlag.NoUid;
    if (n.managed) f |= NodeFlag.Managed;
    if (cut.has(i)) f |= NodeFlag.EdgeCut;
    if (unresolved.has(key)) f |= NodeFlag.Unresolved;
    flags[i] = f;
  });

  const columns = columnsByType(nodes);
  const typeNames = [...columns.keys()];
  const typeIdx = new Uint16Array(count);
  const pos = new Float32Array(count * 2);

  typeNames.forEach((t, ci) => {
    for (const i of columns.get(t)!) {
      typeIdx[i] = ci;
      const ri = columns.get(t)!.indexOf(i);
      pos[i * 2] = PAD + ci * COL_W;
      pos[i * 2 + 1] = PAD + ri * ROW_H;
    }
  });

  const maxRows = Math.max(1, ...typeNames.map((t) => columns.get(t)!.length));
  const full = {
    width: PAD * 2 + Math.max(1, typeNames.length) * COL_W,
    height: PAD * 2 + maxRows * ROW_H,
  };

  // Folding is per type, and expanding a type has to actually unfold it: `loose` is the set of
  // nodes a frame draws, `groups` the set it draws instead of the folded ones. Both arms read these,
  // so "点选逐组展开" is one behaviour rather than two.
  const expanded = opts.expandedTypes ?? new Set<string>();
  const folding = count > threshold;
  const folded = new Set(typeNames.filter((t) => folding && !expanded.has(t)));

  const groupList: NonNullable<GraphScene['groups']> = [];
  const loose: number[] = [];
  typeNames.forEach((t, ci) => {
    if (!folded.has(t)) return;
    const list = columns.get(t)!;
    groupList.push({ type: t, count: list.length, x: PAD + ci * COL_W, y: PAD + Math.floor(list.length / 2) * ROW_H, idx: ci });
  });
  for (let i = 0; i < count; i++) {
    if (folded.has(typeNames[typeIdx[i]])) flags[i] |= NodeFlag.Grouped;
    else loose.push(i);
  }
  const groups: GraphScene['groups'] = groupList.length ? groupList : null;

  // CSR adjacency, built once per dataset, so highlighting a node's neighbourhood is O(deg)
  // rather than a scan of every edge on every selection change.
  const outStart = new Int32Array(count + 1);
  const inStart = new Int32Array(count + 1);
  for (const e of sceneEdges) {
    outStart[e.from + 1]++;
    inStart[e.to + 1]++;
  }
  for (let i = 0; i < count; i++) {
    outStart[i + 1] += outStart[i];
    inStart[i + 1] += inStart[i];
  }
  const outEdge = new Int32Array(sceneEdges.length);
  const inEdge = new Int32Array(sceneEdges.length);
  const outFill = outStart.slice(0, count);
  const inFill = inStart.slice(0, count);
  for (const e of sceneEdges) {
    outEdge[outFill[e.from]++] = e.to;
    inEdge[inFill[e.to]++] = e.from;
  }

  return {
    count,
    pos,
    flags,
    typeIdx,
    keys: nodes.map((n) => String(n.businessKey)),
    types: typeNames,
    fields,
    edges: sceneEdges,
    outStart,
    outEdge,
    inStart,
    inEdge,
    groups,
    loose,
    width: full.width,
    height: full.height,
  };
}

/** Neighbours of a node, for the "点选高亮邻接关系" requirement. */
export function neighbours(scene: GraphScene, i: number): { outbound: number[]; inbound: number[] } {
  const outbound: number[] = [];
  const inbound: number[] = [];
  for (let k = scene.outStart[i]; k < scene.outStart[i + 1]; k++) outbound.push(scene.outEdge[k]);
  for (let k = scene.inStart[i]; k < scene.inStart[i + 1]; k++) inbound.push(scene.inEdge[k]);
  return { outbound, inbound };
}

/** Uniform-grid hit index. Node radii are near-constant, so a quadtree would only cost rebuilds. */
export class HitGrid {
  private cells = new Map<number, number[]>();
  constructor(private readonly scene: GraphScene, private readonly cell = 64) {}

  private key(cx: number, cy: number): number {
    return cx * 100003 + cy;
  }

  build(): this {
    this.cells.clear();
    // Only unfolded nodes are hittable: a folded type is reached through its super-node.
    for (const i of this.scene.loose) {
      const x = this.scene.pos[i * 2];
      const y = this.scene.pos[i * 2 + 1];
      const k = this.key(Math.floor(x / this.cell), Math.floor(y / this.cell));
      const bucket = this.cells.get(k);
      if (bucket) bucket.push(i);
      else this.cells.set(k, [i]);
    }
    return this;
  }

  /** Nearest node whose circle contains the point, or -1. */
  query(x: number, y: number, radius = NODE_R): number {
    const cx = Math.floor(x / this.cell);
    const cy = Math.floor(y / this.cell);
    let best = -1;
    let bestD = Infinity;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        const bucket = this.cells.get(this.key(cx + dx, cy + dy));
        if (!bucket) continue;
        for (const i of bucket) {
          const px = this.scene.pos[i * 2];
          const py = this.scene.pos[i * 2 + 1];
          const d = (px - x) ** 2 + (py - y) ** 2;
          if (d <= radius * radius && d < bestD) {
            bestD = d;
            best = i;
          }
        }
      }
    }
    return best;
  }
}

export const NODE_RADIUS = NODE_R;

/** The visible world rectangle, for viewport culling. */
export function worldRect(view: { scale: number; tx: number; ty: number }, cssW: number, cssH: number, margin = 40): { x0: number; y0: number; x1: number; y1: number } {
  return {
    x0: (0 - view.tx) / view.scale - margin,
    y0: (0 - view.ty) / view.scale - margin,
    x1: (cssW - view.tx) / view.scale + margin,
    y1: (cssH - view.ty) / view.scale + margin,
  };
}
