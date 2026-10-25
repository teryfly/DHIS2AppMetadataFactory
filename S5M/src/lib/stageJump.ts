import { computed, toValue, type ComputedRef, type MaybeRefOrGetter } from 'vue';
import { useWorkflowOfProject, useStages } from '@/api/queries';

export interface StageJump {
  /** Route to send the reader to; empty when this workflow has no such stage. */
  to: ComputedRef<string>;
  /** Stage type as S1 named it, for the label. */
  stageType: ComputedRef<string>;
  /** False when the stage type is absent from this workflow (mode A skips PAGE_DESIGN/HANDOFF). */
  available: ComputedRef<boolean>;
  pending: ComputedRef<boolean>;
}

/**
 * UM-REV-007 needs a working button, not a sentence: resolve the `requiredStage` S1 names into a
 * real stage of this project's workflow. Never invent an id — if the stage type is not in this
 * workflow, the caller disables the button and says so.
 */
export function useRequiredStageJump(
  projectId: MaybeRefOrGetter<string>,
  requiredStage: MaybeRefOrGetter<string | undefined>,
): StageJump {
  const workflow = useWorkflowOfProject(projectId);
  const workflowId = computed(() => workflow.data.value?.id ?? '');
  const stages = useStages(workflowId);

  const stageId = computed(() => {
    const want = toValue(requiredStage);
    if (!want) return '';
    const hit = (stages.data.value?.items ?? []).find((s) => String(s.stageType) === want);
    return String(hit?.id ?? '');
  });

  return {
    to: computed(() => (stageId.value ? `/stages/${encodeURIComponent(stageId.value)}` : '')),
    stageType: computed(() => String(toValue(requiredStage) ?? '')),
    available: computed(() => !!stageId.value),
    pending: computed(() => workflow.isFetching.value || stages.isFetching.value),
  };
}
