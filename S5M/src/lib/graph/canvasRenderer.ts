import { NODE_RADIUS, worldRect, type GraphScene, NodeFlag } from './scene';

export interface ViewState {
  scale: number;
  tx: number;
  ty: number;
}

export interface DrawOptions {
  selected: number;
  /** Neighbours of `selected`; empty when nothing is selected. */
  highlight: Set<number>;
  /** Search hits, kept separate so locating an object never redraws the model. */
  matches: Set<string>;
  palette: Palette;
  dpr: number;
  cssW: number;
  cssH: number;
  reducedMotion: boolean;
}

export interface Palette {
  edge: string;
  edgeHighlight: string;
  node: string;
  orphan: string;
  noUid: string;
  /** A reference that leaves this node and goes nowhere it can draw. */
  unresolved: string;
  selected: string;
  match: string;
  text: string;
  surface: string;
}

/** Labels only above this zoom: at 0.4 the text of 5,000 nodes is unreadable anyway. */
const LABEL_SCALE = 0.62;

/**
 * Canvas-2D frame. Cost per frame is O(viewport), not O(document):
 *  - the element is never resized during a gesture (the SVG version mutated width/height every
 *    wheel tick, which forces a full re-layout and raster of a canvas hundreds of thousands of
 *    pixels wide);
 *  - nodes outside the visible world rectangle are skipped;
 *  - text is drawn only when it can be read and only where it matters.
 */
export function draw(ctx: CanvasRenderingContext2D, scene: GraphScene, view: ViewState, o: DrawOptions): void {
  const { dpr, cssW, cssH, palette } = o;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  ctx.setTransform(dpr * view.scale, 0, 0, dpr * view.scale, dpr * view.tx, dpr * view.ty);

  const rect = worldRect(view, cssW, cssH, 60);
  const hasSelection = o.selected >= 0;
  // One mixed pass: folded types paint as super-nodes, everything else as nodes. A fully folded
  // graph draws only super-nodes and no edges, which is what the old all-or-nothing branch did.
  const grouped = NodeFlag.Grouped;

  // --- edges, batched per (highlighted?, kind) so one path covers thousands of lines
  {
    for (const pass of [false, true]) {
      ctx.beginPath();
      ctx.strokeStyle = pass ? palette.edgeHighlight : palette.edge;
      ctx.lineWidth = (pass ? 1.8 : 1) / view.scale;
      let drawn = 0;
      for (const e of scene.edges) {
        if ((scene.flags[e.from] & grouped) !== 0 || (scene.flags[e.to] & grouped) !== 0) continue;
        const fx = scene.pos[e.from * 2];
        const fy = scene.pos[e.from * 2 + 1];
        const tx = scene.pos[e.to * 2];
        const ty = scene.pos[e.to * 2 + 1];
        if (Math.max(fx, tx) < rect.x0 || Math.min(fx, tx) > rect.x1 || Math.max(fy, ty) < rect.y0 || Math.min(fy, ty) > rect.y1) continue;
        const lit = hasSelection && (e.from === o.selected || e.to === o.selected);
        if (lit !== pass) continue;
        ctx.moveTo(fx, fy);
        ctx.lineTo(tx, ty);
        drawn++;
      }
      if (drawn) ctx.stroke();
    }
  }

  // --- nodes (or group super-nodes)
  const r = NODE_RADIUS / view.scale;
  for (const g of scene.groups ?? []) {
      ctx.beginPath();
      ctx.arc(g.x, g.y, Math.min(46, 18 + Math.sqrt(g.count) * 3) / view.scale, 0, Math.PI * 2);
      ctx.fillStyle = palette.node;
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.fillStyle = palette.surface;
      ctx.font = `${13 / view.scale}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(g.count), g.x, g.y);
      ctx.fillStyle = palette.text;
      ctx.fillText(g.type, g.x, g.y + 34 / view.scale);
    }
  {
    // Dimmed pass first so the highlighted subset can be drawn over it without a second scan.
    for (const pass of [false, true]) {
      for (const i of scene.loose) {
        const x = scene.pos[i * 2];
        const y = scene.pos[i * 2 + 1];
        if (x < rect.x0 || x > rect.x1 || y < rect.y0 || y > rect.y1) continue;
        const lit = i === o.selected || o.highlight.has(i);
        if (lit !== pass) continue;
        const f = scene.flags[i];
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = f & NodeFlag.NoUid ? palette.noUid : f & NodeFlag.Orphan ? palette.orphan : palette.node;
        ctx.globalAlpha = hasSelection && !lit ? 0.35 : 1;
        ctx.fill();
        if (lit) {
          ctx.lineWidth = 2 / view.scale;
          ctx.strokeStyle = i === o.selected ? palette.selected : palette.edgeHighlight;
          ctx.stroke();
        }
        if (f & (NodeFlag.Unresolved | NodeFlag.EdgeCut)) {
          ctx.beginPath();
          ctx.moveTo(x + r, y);
          ctx.lineTo(x + r * 2.4, y);
          ctx.strokeStyle = palette.unresolved;
          ctx.lineWidth = 2 / view.scale;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        const isMatch = o.matches.size > 0 && o.matches.has(scene.keys[i]);
        if ((view.scale > LABEL_SCALE && (lit || isMatch)) || isMatch) {
          if (isMatch) {
            ctx.beginPath();
            ctx.arc(x, y, r * 1.7, 0, Math.PI * 2);
            ctx.strokeStyle = palette.match;
            ctx.lineWidth = 2 / view.scale;
            ctx.stroke();
          }
          // A search hit is labelled even when zoomed out: locating a node is the whole point.
          ctx.fillStyle = palette.text;
          ctx.font = `${12 / view.scale}px system-ui, sans-serif`;
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText(scene.keys[i], x + r * 1.4, y);
        }
      }
    }
  }

  void o.reducedMotion;
}
