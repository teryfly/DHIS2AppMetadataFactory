import type { ProjectView, WorkflowView } from '@/api/types';

/** Outlet context shared from ProjectWorkspace to its child routes. */
export interface WorkspaceContext {
  projectId: string;
  workflowId?: string;
  project: ProjectView;
  workflow?: WorkflowView;
}
