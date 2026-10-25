/*
 * Convenience over the S1 reads a project screen needs. Everything here is server-derived via
 * TanStack Query (UI-APP-006); nothing business is stored locally.
 */
import { useQuery } from '@tanstack/react-query';
import { projectOptions, workflowOptions } from '@/api/queries';
import { useAllowedActions } from '@/api/actions';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';

export function useProjectWorkflow(projectId: string | undefined) {
  const project = useQuery(projectOptions(projectId ?? ''));
  const workflow = useQuery(workflowOptions(projectId ?? ''));
  const workflowId = workflow.data?.id;
  // Live refresh for the current project (UI-RT-001..005).
  const { status } = useWorkflowEvents({ workflowId, projectId });

  return {
    project,
    workflow,
    workflowId,
    stages: workflow.data?.stages ?? project.data?.stages ?? [],
    currentStageId: (workflow.data?.currentStageId ?? project.data?.currentStageId) ?? undefined,
    sseStatus: status,
  };
}

export { useAllowedActions };
