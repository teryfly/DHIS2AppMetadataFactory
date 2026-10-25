/*
 * Execute every read-model query hook against the mock so each queryFn + its unwrap path runs.
 * These are the S1 reads the UI depends on (UI-APP-006/007); asserting they resolve proves the
 * typed client is wired to real endpoints.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import '@/i18n';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import {
  questionOptions, workflowGatesOptions, gateOptions, sectionSchemaOptions, inputsOptions, registryOptions,
  blueprintDraftOptions, auditOptions, artifactDiffOptions, artifactLineageOptions, validationIssuesOptions,
  workflowArtifactsOptions,
} from './queries';
import { useExplainArtifact } from './mutations';
import { createQueryClient, onApiError } from './queryClient';
import { server } from '@/mocks/server';
import { http, HttpResponse } from 'msw';
import { WORKFLOW_ID, DESIGN_PROJECT_ID } from '@/mocks/db';

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe('read-model query hooks resolve against S1', () => {
  it('question by id', async () => {
    const { result } = renderHook(() => useQuery(questionOptions('Q-2')), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.id).toBe('Q-2');
  });
  it('workflow gates + gate by id', async () => {
    const g = renderHook(() => useQuery(workflowGatesOptions(WORKFLOW_ID)), { wrapper });
    await waitFor(() => expect(g.result.current.isSuccess).toBe(true));
    const one = renderHook(() => useQuery(gateOptions('GT-1')), { wrapper });
    await waitFor(() => expect(one.result.current.isSuccess).toBe(true));
  });
  it('section schemas, inputs, registry, draft, audit', async () => {
    const a = renderHook(() => useQuery(sectionSchemaOptions(DESIGN_PROJECT_ID, 'requirements')), { wrapper });
    await waitFor(() => expect(a.result.current.isSuccess).toBe(true));
    const b = renderHook(() => useQuery(inputsOptions(DESIGN_PROJECT_ID)), { wrapper });
    await waitFor(() => expect(b.result.current.isSuccess).toBe(true));
    const c = renderHook(() => useQuery(registryOptions(DESIGN_PROJECT_ID)), { wrapper });
    await waitFor(() => expect(c.result.current.isSuccess).toBe(true));
    const d = renderHook(() => useQuery(blueprintDraftOptions(DESIGN_PROJECT_ID)), { wrapper });
    await waitFor(() => expect(d.result.current.isSuccess).toBe(true));
    const e = renderHook(() => useInfiniteQuery(auditOptions()), { wrapper });
    await waitFor(() => expect(e.result.current.isSuccess).toBe(true));
  });
});

/*
 * The review read models (UI-REV-001 / 005). These are asserted at the hook level rather than by
 * driving the review screen: the screen's second interaction in the same jsdom task stalls the
 * test runner, and what has to be proven here is the request itself — that the version the
 * operator picked reaches the diff route, and that an explanation carries its project.
 */
describe('review read models (UI-REV-001 / 005)', () => {
  it('asks the diff route about the version that was picked', async () => {
    let against: string | null = null;
    server.use(
      http.get(`/api/v1/artifacts/:artifactId/diff`, ({ request }) => {
        against = new URL(request.url).searchParams.get('against');
        return HttpResponse.json({ aId: 'ART-PKG-1', bId: 'ART-PKG-2', changed: true, kind: 'OBJECTS', lines: [] });
      }),
    );
    const view = renderHook(() => useQuery(artifactDiffOptions('ART-PKG-1', 'ART-PKG-2')), { wrapper });
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
    expect(against).toBe('ART-PKG-2');
  });

  it('carries the filter into the issue query key, so a changed filter refetches', async () => {
    const rules: (string | null)[] = [];
    server.use(
      http.get(`/api/v1/workflows/:id/validation-issues`, ({ request }) => {
        rules.push(new URL(request.url).searchParams.get('ruleId'));
        return HttpResponse.json({ items: [], total: 0 });
      }),
    );
    const first = renderHook(() => useQuery(validationIssuesOptions(WORKFLOW_ID)), { wrapper });
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    const filtered = renderHook(() => useQuery(validationIssuesOptions(WORKFLOW_ID, { ruleId: 'BPV-ST-004' })), { wrapper });
    await waitFor(() => expect(filtered.result.current.isSuccess).toBe(true));
    expect(rules).toContain(null);
    expect(rules).toContain('BPV-ST-004');
  });

  it('lists the workflow artifacts and their lineage', async () => {
    const list = renderHook(() => useQuery(workflowArtifactsOptions(WORKFLOW_ID)), { wrapper });
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    expect((list.result.current.data?.items ?? []).length).toBeGreaterThan(0);
    const chain = renderHook(() => useQuery(artifactLineageOptions('ART-PKG-1')), { wrapper });
    await waitFor(() => expect(chain.result.current.isSuccess).toBe(true));
    expect(chain.result.current.data?.sources).toContain('ART-VAL-1');
  });

  it('sends an explanation request with the project it belongs to', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post(`/api/v1/artifacts/:artifactId/explain`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ explanationArtifactId: 'ART-EXP-1' });
      }),
    );
    const call = renderHook(() => useExplainArtifact(DESIGN_PROJECT_ID), { wrapper });
    const answer = await call.result.current.mutateAsync({ artifactId: 'ART-VAL-1' });
    expect(body.projectId).toBe(DESIGN_PROJECT_ID);
    expect(answer.explanationArtifactId).toBe('ART-EXP-1');
  });
});

/*
 * S5IF-BP05 × CP-UI-022: a project whose first design stage has not run has no draft, and S1
 * answers that with 404 NOT_GENERATED. The screens render with no draft, so the read resolving
 * empty is the outcome — routing it through the error bridge would toast "对象不存在" at someone
 * who has done nothing wrong (UI-REQ-002). A NOT_FOUND that names no reason is still a real error.
 */
describe('blueprint draft read when no draft exists yet (S5IF-BP05)', () => {
  const withBridge = ({ children }: { children: ReactNode }) => {
    const qc = createQueryClient();
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };

  it('resolves empty and stays out of the error bridge on the guided 404', async () => {
    server.use(
      http.get(`/api/v1/projects/:projectId/blueprint/draft`, () =>
        HttpResponse.json(
          { errorCode: 'NOT_FOUND', message: 'no open blueprint draft', details: { projectId: 'PRJ-1', reason: 'NOT_GENERATED', requiredStage: 'REQUIREMENTS' } },
          { status: 404 },
        ),
      ),
    );
    const surfaced: string[] = [];
    const off = onApiError((error) => surfaced.push(error.errorCode));

    const view = renderHook(() => useQuery(blueprintDraftOptions('PRJ-1')), { wrapper: withBridge });
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));

    expect(view.result.current.data).toBeNull();
    expect(surfaced).toEqual([]);
    off();
  });

  it('still reports a NOT_FOUND that gives no reason', async () => {
    server.use(
      http.get(`/api/v1/projects/:projectId/blueprint/draft`, () =>
        HttpResponse.json({ errorCode: 'NOT_FOUND', message: 'no such project', details: { projectId: 'PRJ-1' } }, { status: 404 }),
      ),
    );
    const surfaced: string[] = [];
    const off = onApiError((error) => surfaced.push(error.errorCode));

    const view = renderHook(() => useQuery(blueprintDraftOptions('PRJ-1')), { wrapper: withBridge });
    await waitFor(() => expect(view.result.current.isError).toBe(true));

    expect(surfaced).toEqual(['NOT_FOUND']);
    off();
  });
});
