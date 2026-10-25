/*
 * UI-REV-002 / UI-REV-006: the object graph screen. What is asserted here is the reading of the
 * answer S1 assembles from S2's stored edges — every object listed, the unconnected one marked, a
 * type filter that keeps both ends of a crossing reference, a search that focuses without dropping
 * context, the dangling references shown as annotations rather than as a second issue list, and
 * the guided empty when the build has not produced a graph yet. The canvas itself is exercised in
 * the browser suite; this is the part that must be true whatever draws it.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

const GRAPH_PATH = '/api/v1/workflows/:id/object-graph';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

/** the object list is the accessible half of the screen, so it is also the half these tests read */
async function objectList() {
  return within(await screen.findByRole('region', { name: 'Objects in this graph' }));
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('Object graph (UI-REV-002)', () => {
  it('lists every object in the answer and names the unconnected one', async () => {
    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    const list = await objectList();

    expect(list.getByText('entity:facility')).toBeInTheDocument();
    expect(list.getByText('form:anc')).toBeInTheDocument();
    expect(list.getByText('unconnected')).toBeInTheDocument();
    expect(await screen.findByText(/3 objects, 2 references, 1 unconnected/)).toBeInTheDocument();
  });

  it('says which build the graph came from', async () => {
    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    await objectList();

    // the graph is a build's output, so an answer without its provenance cannot be acted on
    expect(await screen.findByText(/Baseline compile · 0\.1\.0 · Build bld-0001/)).toBeInTheDocument();
  });

  it('sends the object-type filter to S2 and keeps both ends of a crossing reference', async () => {
    const asked: string[][] = [];
    server.use(
      http.get(GRAPH_PATH, ({ request }) => {
        const sp = new URL(request.url).searchParams;
        asked.push(sp.getAll('dhis2Type'));
        return HttpResponse.json(graphResponse(sp));
      }),
    );

    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    await objectList();
    await userEvent.click(await screen.findByRole('checkbox', { name: /organisationUnit/ }));

    const list = await objectList();
    await waitFor(() => expect(asked).toContainEqual(['organisationUnit']));
    // the programRule end stays, so the crossing edge still renders
    await waitFor(() => expect(list.getByText('entity:facility')).toBeInTheDocument());
    expect(list.getByText('rule:age-range')).toBeInTheDocument();
    expect(list.queryByText('form:anc')).not.toBeInTheDocument();
  });

  it('searches inside the loaded graph without asking S1 again', async () => {
    let reads = 0;
    server.use(
      http.get(GRAPH_PATH, ({ request }) => {
        reads += 1;
        return HttpResponse.json(graphResponse(new URL(request.url).searchParams));
      }),
    );

    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    await objectList();
    const before = reads;
    await userEvent.type(screen.getByLabelText(/Find an object/), 'facility');

    const list = await objectList();
    await waitFor(() => expect(list.getByText('entity:facility')).toBeInTheDocument());
    // one edge away stays visible, so the hit can be read in context
    expect(list.getByText('rule:age-range')).toBeInTheDocument();
    expect(list.queryByText('form:anc')).not.toBeInTheDocument();
    // S1 takes no query string any more: typing must not turn into a request per keystroke
    expect(reads).toBe(before);
  });

  it('shows dangling references as an annotation on the graph, not as issues', async () => {
    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    await objectList();

    const box = await screen.findByText(/Unresolved references \(1\)/);
    expect(within(box.parentElement as HTMLElement).getByText(/rule:triage/)).toBeInTheDocument();
    expect(box).toBeInTheDocument();
    // the object carrying the dangling reference is tagged on the list too
    const list = await objectList();
    expect(list.getByText('dangling ref')).toBeInTheDocument();
  });

  it('guides to the stage that produces the graph when nothing is compiled yet', async () => {
    server.use(
      http.get(GRAPH_PATH, () =>
        HttpResponse.json(
          { errorCode: 'NOT_FOUND', message: 'no graph', details: { reason: 'NOT_GENERATED', requiredStage: 'BASELINE_COMPILE' } },
          { status: 404 },
        )),
    );

    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    expect(await screen.findByText(/Nothing to draw yet/)).toBeInTheDocument();
    // the stage's own label also appears in the workspace nav, so assert the sentence and the
    // button rather than the bare word
    expect(await screen.findByText(/The graph exists once the blueprint has been compiled/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /Go to Baseline compile/ })).toBeInTheDocument();
  });

  it('jumps from an object to its Registry entry', async () => {
    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/graph`);
    const list = await objectList();

    await userEvent.click(list.getByRole('button', { name: /rule:age-range/ }));

    await screen.findByText(/Showing the object rule:age-range/);
    expect(screen.getByRole('button', { name: /Show all objects/ })).toBeInTheDocument();
    // the metadata screen filtered to the one object the graph pointed at
    expect(await screen.findByText('AGE_RULE')).toBeInTheDocument();
    expect(screen.queryByText('FACILITY')).not.toBeInTheDocument();
  });
});

/** The mock's own narrowing, replayed so a test can assert what the screen asked for. */
function graphResponse(sp: URLSearchParams) {
  const types = sp.getAll('dhis2Type');
  const nodes = db.objectGraph.nodes.map((n) => ({ ...n }));
  const edges = db.objectGraph.edges.map((e) => ({ ...e }));
  let kept = nodes;
  let keptEdges = edges;
  if (types.length > 0) {
    const keep = new Set(nodes.filter((n) => types.includes(String(n.dhis2Type))).map((n) => String(n.businessKey)));
    for (const e of edges) {
      if (keep.has(String(e.fromBusinessKey))) keep.add(String(e.toBusinessKey));
      if (keep.has(String(e.toBusinessKey))) keep.add(String(e.fromBusinessKey));
    }
    kept = nodes.filter((n) => keep.has(String(n.businessKey)));
    keptEdges = edges.filter((e) => keep.has(String(e.fromBusinessKey)) && keep.has(String(e.toBusinessKey)));
  }
  return { ...db.objectGraph, nodes: kept, edges: keptEdges, filters: { dhis2Types: types, businessKeys: [] } };
}
