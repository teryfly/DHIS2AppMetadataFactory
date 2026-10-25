import { computed, toValue, type MaybeRefOrGetter } from 'vue';
import { useWorkflowOfProject, useStages, useAllowedActions } from '@/api/queries';
import { useWorkflowEvents } from '@/sse/useWorkflowEvents';

/**
 * Resolves a project's workflow (never stored locally — re-read from S1 each time, UM-P-01), wires
 * the SSE stream for that workflow, and returns stages + the live action gate.
 */
export function useProjectWorkflow(projectId: MaybeRefOrGetter<string>) {
  const workflow = useWorkflowOfProject(projectId);
  const workflowId = computed(() => toValue(workflow.data.value?.id) ?? '');
  const stages = useStages(workflowId);
  const actions = useAllowedActions(workflowId);
  useWorkflowEvents({ workflowId, projectId, enabled: computed(() => !!workflowId.value) });

  const currentStageId = computed(() => workflow.data.value?.currentStageId ?? '');

  return { workflow, workflowId, stages, actions, currentStageId };
}
