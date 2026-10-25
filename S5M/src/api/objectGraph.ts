import { computed, toValue, type MaybeRefOrGetter } from 'vue';
import { apiGet } from '@/api/http';
import { qk } from '@/api/keys';
import { useQuery } from '@tanstack/vue-query';
import type * as T from '@/api/types';

/**
 * CP-UI-020 (BP-TD-009): the dependency graph one build produced, read by workflow.
 *
 *   GET /api/v1/workflows/{workflow_id}/object-graph?runId=&blueprintVersion=&dhis2Type=&businessKey=
 *
 * `runId` and `blueprintVersion` select the build and are mutually exclusive — S1 answers 422 for
 * both — and with neither it serves the current version's latest baseline compile. The type and
 * business-key filters narrow server-side to the matched nodes plus their one-hop edges, while
 * `orphans` and `unresolvedRegistryRefs` stay properties of the whole build. "No graph yet" is a
 * 404 NOT_GENERATED from S1, never this view with empty lists (CP-UI-022).
 */
export interface GraphQuery {
  runId?: string;
  blueprintVersion?: string;
  dhis2Type?: string[];
  businessKey?: string[];
}

export function useObjectGraphForWorkflow(
  workflowId: MaybeRefOrGetter<string>,
  params: MaybeRefOrGetter<GraphQuery | undefined>,
) {
  return useQuery({
    queryKey: computed(() => qk.workflowGraph(toValue(workflowId), toValue(params) ?? null)),
    enabled: computed(() => !!toValue(workflowId)),
    staleTime: 30_000,
    queryFn: () => {
      const p = toValue(params) ?? {};
      return apiGet<T.WorkflowObjectGraphView>(
        `/workflows/${encodeURIComponent(toValue(workflowId))}/object-graph`,
        {
          query: {
            runId: p.runId || undefined,
            blueprintVersion: p.blueprintVersion || undefined,
            dhis2Type: p.dhis2Type?.length ? p.dhis2Type : undefined,
            businessKey: p.businessKey?.length ? p.businessKey : undefined,
          },
        },
      );
    },
  });
}
