/*
 * UI-REV-002 (FR-UI-005, BP-TD-009): the dependency graph one build produced. Nodes are Registry
 * objects and edges are the field-level references S2 stores, so the picture is the model's own
 * dependency graph rather than a drawing the browser inferred.
 *
 * Three things this screen refuses to do:
 *  - infer relationships locally: S2 narrows the graph and returns the matched nodes with their
 *    one-hop edges, and the browser only ever prunes inside what that read already returned;
 *  - render more than the model can carry: above the collapse threshold the view groups by object
 *    type and asks for a filter instead of drawing thousands of nodes (UI-REV-006);
 *  - be the only way to read the graph: the same answer is listed as text below the canvas, which
 *    is what a screen reader and a keyboard operator actually navigate (WCAG 1.1.1).
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ReactFlow, Background, Controls, Handle, Position, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button, Card, NoticeBox, Tag } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { EmptyState } from '@/components/EmptyState';
import { VirtualTable } from '@/components/VirtualTable';
import { Loading } from '@/lib/PageContainer';
import { TextInput } from '@/components/form';
import { objectGraphOptions } from '@/api/queries';
import { isApiError } from '@/api/errors';
import type { GraphNodeView, UnresolvedReferenceView } from '@/api/types';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';

/** Above this, drawing every node is a picture nobody can read; the view groups by type instead. */
const COLLAPSE_ABOVE = 500;

/** `BuildKind` as S2 stores it. An unknown value is shown as sent rather than guessed away. */
const KIND_KEYS: Record<string, string> = {
  BASELINE: 'graph.kindBaseline',
  ENVIRONMENT: 'graph.kindEnvironment',
};

export function ObjectGraph() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { projectId, workflowId, project } = useOutletContext<WorkspaceContext>();
  const [types, setTypes] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<string | null>(null);

  const graph = useQuery(objectGraphOptions(projectId, workflowId, { dhis2Types: types }));
  const term = search.trim().toLowerCase();

  const all = (graph.data?.nodes ?? []) as GraphNodeView[];
  const allEdges = graph.data?.edges ?? [];
  const orphans = useMemo(() => new Set(graph.data?.orphans ?? []), [graph.data]);
  const dangling = (graph.data?.unresolvedRegistryRefs ?? []) as UnresolvedReferenceView[];
  const danglingKeys = useMemo(() => new Set(dangling.map((r) => r.fromBusinessKey)), [dangling]);

  // S1 stopped taking a query string, so the search works inside what this read returned: a hit
  // keeps its own one-hop neighbours, because a node shown without what it refers to is misleading
  const visible = useMemo(() => {
    if (!term) return { nodes: all, edges: allEdges };
    const hits = all.filter((n) => matches(n, term)).map((n) => String(n.businessKey));
    const keep = new Set(hits);
    for (const edge of allEdges) {
      if (keep.has(String(edge.fromBusinessKey))) keep.add(String(edge.toBusinessKey));
      if (keep.has(String(edge.toBusinessKey))) keep.add(String(edge.fromBusinessKey));
    }
    return {
      nodes: all.filter((n) => keep.has(String(n.businessKey))),
      edges: allEdges.filter((e) => keep.has(String(e.fromBusinessKey)) && keep.has(String(e.toBusinessKey))),
    };
  }, [all, allEdges, term]);

  const byType = useMemo(() => groupByType(visible.nodes), [visible.nodes]);
  const collapsed = visible.nodes.length > COLLAPSE_ABOVE && types.length === 0;

  // only the first read blocks the screen: while a filter or a search is in flight the previous
  // picture stays, which is what keeps the control the operator is typing into on screen
  if (graph.isPending && !graph.data) return <Loading />;

  // "no graph yet" is a 404 NOT_GENERATED from S1, never an empty view (CP-UI-022): the screen has
  // to say which stage produces it and get the operator there, not show a failed request
  const missing = isApiError(graph.error) && graph.error.errorCode === 'NOT_FOUND' ? graph.error : null;
  if (missing) {
    const reason = String(missing.details.reason ?? 'NOT_GENERATED');
    const requiredStage = String(missing.details.requiredStage ?? 'BASELINE_COMPILE');
    const stage = project.stages?.find((s) => s.stageType === requiredStage);
    const label = t(`stageTypes.${requiredStage}`, { defaultValue: requiredStage });
    return (
      <Card>
        <EmptyState>
          <div className="stack">
            <Heading level={3} size="medium">{t('graph.notGeneratedTitle')}</Heading>
            <p>{reason === 'NOT_EXIST' ? t('graph.notExistBody') : t('graph.notGeneratedBody', { stage: label })}</p>
            {stage?.id ? (
              <Button
                primary
                onClick={() => navigate(`/projects/${projectId}/stages/${String(stage.id)}`)}
              >
                {t('graph.notGeneratedAction', { stage: label })}
              </Button>
            ) : null}
          </div>
        </EmptyState>
      </Card>
    );
  }

  const flowNodes: Node[] = collapsed
    ? [...byType.entries()].map(([type, rows], column) => ({
        id: type,
        position: { x: column * 240, y: 0 },
        data: { label: `${type} · ${rows.length}`, kind: 'group' },
        type: 'group',
      }))
    : visible.nodes.map((node, index) => ({
        id: String(node.businessKey),
        // grouped by type so a column is one object type: the layout is the model's own structure,
        // not a force-directed guess that would move every time the data changed
        position: { x: columnOf(byType, String(node.dhis2Type ?? '—')) * 260, y: (index % 14) * 76 },
        data: {
          label: String(node.businessKey),
          kind: 'object',
          dhis2Type: node.dhis2Type,
          orphan: orphans.has(String(node.businessKey)),
          dangling: danglingKeys.has(String(node.businessKey)),
          active: selected === String(node.businessKey),
        },
        type: 'object',
      }));

  const flowEdges: Edge[] = visible.edges.map((edge, i) => {
    const from = String(edge.fromBusinessKey);
    const to = String(edge.toBusinessKey);
    const touches = selected !== null && (from === selected || to === selected);
    const source = collapsed ? String(byType.get(String(edge.fromDhis2Type ?? '—'))?.[0]?.dhis2Type ?? '—') : from;
    const target = collapsed ? String(edge.toDhis2Type ?? '—') : to;
    return {
      id: `${from}->${to}-${String(edge.field)}-${i}`,
      source,
      target,
      label: edge.field,
      animated: touches,
      style: touches ? { strokeWidth: 2, stroke: '#1a4a80' } : { stroke: '#8b959c' },
    };
  });

  const build = graph.data;

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>{t('graph.title')}</h2>
          <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>{t('page.graph')}</p>
        </div>
        {/* which build this is: the graph is a build's output, so the answer is meaningless without
            the version, the build id and the kind that produced it */}
        <span className="muted mono" style={{ fontSize: 12 }}>
          {t(KIND_KEYS[String(build?.kind ?? '')] ?? 'graph.kindUnknown')}
          {build?.blueprintVersion ? ` · ${String(build.blueprintVersion)}` : ''}
          {build?.environmentKey ? ` · ${String(build.environmentKey)}` : ''}
          {build?.buildId ? ` · ${t('graph.buildId')} ${short(String(build.buildId))}` : ''}
          {build?.runId ? ` · ${t('graph.buildRun')} ${short(String(build.runId))}` : ''}
          {` · v${String(build?.schemaVersion ?? 1)}`}
        </span>
      </div>

      <Card>
        <div className="stack">
          <div style={{ maxWidth: 360 }}>
            <TextInput
              label={t('graph.search')}
              value={search}
              onChange={setSearch}
              helpText={t('graph.searchHint')}
            />
          </div>

          {byType.size > 1 ? (
            <fieldset className="candidate-set" tabIndex={0} aria-label={t('graph.filterTypes')}>
              <legend className="muted" style={{ fontSize: 13 }}>{t('graph.filterTypes')}</legend>
              {[...byType.keys()].map((type) => (
                <label key={type} className="candidate">
                  <input
                    type="checkbox"
                    checked={types.includes(type)}
                    onChange={() =>
                      setTypes((prev) => (prev.includes(type) ? prev.filter((x) => x !== type) : [...prev, type]))
                    }
                  />
                  <span>{type}</span>
                  <span className="muted">{String(byType.get(type)?.length ?? 0)}</span>
                </label>
              ))}
            </fieldset>
          ) : null}

          {collapsed ? (
            <NoticeBox warning title={t('graph.collapsedTitle', { count: visible.nodes.length })}>
              {t('graph.collapsedBody')}
            </NoticeBox>
          ) : null}

          {dangling.length ? (
            /* an annotation on the graph, never a second issue list: the issues an operator acts on
               are the validation ones (BP-TD-009, CP-UI-004), so this box stays deliberately neutral
               rather than borrowing the warning styling of a finding */
            <NoticeBox title={t('graph.dangling', { count: dangling.length })}>
              <div className="stack">
                <p>{t('graph.danglingBody')}</p>
                <ul className="issue-list">
                  {dangling.slice(0, 20).map((r, i) => (
                    <li key={`${String(r.fromBusinessKey)}-${String(r.field)}-${i}`}>
                      <span className="mono">{String(r.fromBusinessKey)}</span>
                      {` · ${t('graph.danglingField')} `}
                      <span className="mono">{String(r.field)}</span>
                      {` → ${String(r.target)}`}
                      <button
                        type="button"
                        className="disclosure"
                        onClick={() =>
                          navigate(`/projects/${projectId}/metadata?object=${encodeURIComponent(String(r.fromBusinessKey))}`)
                        }
                      >
                        {t('graph.object')}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </NoticeBox>
          ) : null}
        </div>
      </Card>

      {!collapsed ? (
        /* the canvas is the one part that measures the DOM, so it fails on its own: the object
           list below stays readable when it does (WCAG 1.1.1 needs the text anyway) */
        <ErrorBoundary>
          <section aria-label={t('graph.canvasAria')} className="graph-canvas" tabIndex={0}>
            <ReactFlow
              nodes={flowNodes}
              edges={flowEdges}
              nodeTypes={NODE_TYPES}
              onNodeClick={(_event, node) => {
                if (node.type === 'group') {
                  setTypes([String(node.id)]);
                  setSelected(null);
                  return;
                }
                setSelected(String(node.id));
              }}
              onPaneClick={() => setSelected(null)}
              fitView
              minZoom={0.2}
              proOptions={{ hideAttribution: true }}
            >
              <Background />
              <Controls aria-label={t('graph.controls')} />
            </ReactFlow>
          </section>
        </ErrorBoundary>
      ) : null}

      {/* the same answer as text: what a screen reader navigates, and what the canvas summarises */}
      <Card>
        <div className="stack">
          <Heading level={5} size="small">{t('graph.summary')}</Heading>
          <ul className="issue-list">
            <li>{t('graph.counts', {
              nodes: visible.nodes.length,
              edges: visible.edges.length,
              orphans: orphans.size,
            })}</li>
          </ul>
          <VirtualTable
            rows={visible.nodes}
            ariaLabel={t('graph.nodesTable')}
            captionNote={build?.blueprintVersion ? String(build.blueprintVersion) : undefined}
            columns={[
              {
                key: 'businessKey',
                header: t('graph.object'),
                isRowHeader: true,
                cell: (node) => (
                  <>
                    <button
                      type="button"
                      className="disclosure"
                      onClick={() =>
                        navigate(`/projects/${projectId}/metadata?object=${encodeURIComponent(String(node.businessKey))}`)
                      }
                    >
                      {String(node.businessKey)}
                    </button>
                    {orphans.has(String(node.businessKey)) ? <Tag>{t('graph.orphan')}</Tag> : null}
                    {danglingKeys.has(String(node.businessKey)) ? <Tag>{t('graph.danglingTag')}</Tag> : null}
                    {node.inPayload ? <Tag>{t('graph.inPayload')}</Tag> : null}
                    {!node.uid ? <Tag>{t('graph.noUid')}</Tag> : null}
                  </>
                ),
              },
              { key: 'dhis2Type', header: t('graph.type'), cell: (node) => String(node.dhis2Type ?? '—') },
              { key: 'inDegree', header: t('graph.referencesIn'), cell: (node) => node.inDegree ?? 0 },
              { key: 'outDegree', header: t('graph.referencesOut'), cell: (node) => node.outDegree ?? 0 },
              {
                key: 'uid',
                header: t('graph.uid'),
                cell: (node) => <span className="mono">{node.uid ? String(node.uid) : '—'}</span>,
              },
            ]}
          />
        </div>
      </Card>
    </div>
  );
}

function ObjectNode({ data }: { data: Record<string, unknown> }) {
  const orphan = Boolean(data.orphan);
  const dangling = Boolean(data.dangling);
  const active = Boolean(data.active);
  return (
    <div
      className={`graph-node${orphan ? ' graph-node--orphan' : ''}${dangling ? ' graph-node--dangling' : ''}${
        active ? ' graph-node--active' : ''
      }`}
      title={String(data.label ?? '')}
    >
      <Handle type="target" position={Position.Left} />
      <span className="graph-node__key">{String(data.label ?? '')}</span>
      <span className="graph-node__type">{String(data.dhis2Type ?? '')}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function GroupNode({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="graph-node graph-node--group" title={String(data.label ?? '')}>
      <Handle type="target" position={Position.Left} />
      <span className="graph-node__key">{String(data.label ?? '')}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const NODE_TYPES = { object: ObjectNode, group: GroupNode };

function matches(node: GraphNodeView, term: string): boolean {
  return [node.businessKey, node.dhis2Type, node.uid, node.disposition]
    .some((value) => String(value ?? '').toLowerCase().includes(term));
}

function short(id: string): string {
  return id.length > 10 ? `${id.slice(0, 10)}…` : id;
}

function groupByType(nodes: GraphNodeView[]): Map<string, GraphNodeView[]> {
  const out = new Map<string, GraphNodeView[]>();
  for (const node of nodes) {
    const type = String(node.dhis2Type ?? '—');
    const bucket = out.get(type) ?? [];
    bucket.push(node);
    out.set(type, bucket);
  }
  // widest column first, so the objects that dominate the model are the ones you see
  return new Map([...out.entries()].sort((a, b) => b[1].length - a[1].length));
}

function columnOf(byType: Map<string, GraphNodeView[]>, type: string): number {
  const index = [...byType.keys()].indexOf(type);
  return index < 0 ? 0 : index;
}
