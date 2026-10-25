import { apiGet } from '@/api/http';

/**
 * S1 action names, kept centralized so the UI never guesses availability and never drifts from the
 * backend contract (UM-BR-01). Values mirror the S1 `allowed-actions` catalog.
 */
export const ACTION = {
  projectEdit: 'project.edit',
  projectArchive: 'project.archive',
  projectDelete: 'project.delete',
  projectExport: 'project.export',
  projectUpgradeMode: 'project.upgrade_mode',
  projectCreate: 'project.create',
  /** S5IF-SEC01…03: the one-time credential entry control (UM-ADM-002 as amended). */
  secretManage: 'secret.manage',
  projectImport: 'project.import',

  inputUpload: 'input.upload',
  inputParse: 'input.parse',
  inputConfirmColumnMapping: 'input.confirm_column_mapping',

  artifactExplain: 'artifact.explain',
  artifactDownload: 'artifact.download',
  // FR-DATA-008: clearing an artifact's bytes is its own right (`iam.ARTIFACT_PURGE`), and it is the
  // one irreversible write on the artifact screen, so it is gated and confirmed separately.
  artifactPurge: 'artifact.purge',
  // CP-UI-021: the only answer that says whether S2 has delivered a development package yet. The
  // Handoff page asks it instead of inferring existence from a 404 (UM-BR-01).
  frontendPackageDownload: 'frontend_package.download',

  deploymentCreate: 'deployment.create',
  deploymentCancel: 'deployment.cancel',
  // S1 answers this per run (`domain/actions.py:495`, `target_id` is the run) and refuses it with
  // `NO_OPEN_RUN` once the run is closed. The re-check button used to be a plain button.
  driftCheck: 'deployment_run.drift_check',

  changeRequestCreate: 'change_request.create',
  changeRequestImpact: 'change_request.impact',
  changeRequestApprove: 'change_request.approve',
  changeRequestReject: 'change_request.reject',
  changeRequestStart: 'change_request.start',
  changeRequestComplete: 'change_request.complete',
  changeRequestCancel: 'change_request.cancel',

  workflowPause: 'workflow.pause',
  workflowResume: 'workflow.resume',
  workflowCancel: 'workflow.cancel',
  agentCancel: 'agent.cancel',

  stageRun: 'stage.run',
  stageRerun: 'stage.rerun',
  stageSubmitForApproval: 'stage.submit_for_approval',
  stageResolveManualCheck: 'stage.resolve_manual_check',
  patchSubmit: 'patch.submit',
  questionAnswer: 'question.answer',
  gateApprove: 'gate.approve',
  gateReject: 'gate.reject',
  executionCommit: 'execution.commit',
  registryBindingSet: 'registry_binding.set',
  uidPoolReplenish: 'uid_pool.replenish',

  environmentCreate: 'environment.create',
  environmentEdit: 'environment.edit',
  environmentDisable: 'environment.disable',
  environmentProbeCapabilities: 'environment.probe_capabilities',
  environmentTestConnection: 'environment.test_connection',
  environmentEmergencyLock: 'environment.emergency_lock',

  governanceModelRegister: 'governance.model.register',
  governanceModelEnable: 'governance.model.enable',
  governancePromptActivate: 'governance.prompt.activate',
  governancePolicyRelax: 'governance.policy.relax',
  governanceProviderRegister: 'governance.provider.register',
  governanceProviderEnable: 'governance.provider.enable',
  governanceRegressionRecord: 'governance.regression.record',
  governanceEvaluationRun: 'governance.evaluation.run',
  governanceBudgetSet: 'governance.budget.set',

  /**
   * The remaining platform-scope names S1 emits for screens this console renders. They live here
   * because UM-BR-01 has no exception clause: a control that cannot name its action cannot be
   * gated, and an ungated control is the UI deciding availability itself.
   * `S1/tests/unit/test_action_vocabulary.py` fails the build if either console invents a name or
   * drops one it uses, which is the only reason two hand-maintained tables can be trusted.
   */
  stageChat: 'stage.chat',
  userManage: 'user.manage',
  projectMemberManage: 'member.manage',
  artifactGrantManage: 'restricted_grant.manage',
  productionExemptionManage: 'production_exemption.manage',
  settingsManage: 'settings.manage',
} as const;

export type ActionName = (typeof ACTION)[keyof typeof ACTION];

export interface ActionEntry {
  action: string;
  available: boolean;
  reasonCode?: string | null;
  details?: Record<string, unknown>;
  targetId?: string | null;
}

export interface AllowedActionsView {
  allowed?: ActionEntry[];
  unavailable?: ActionEntry[];
  scope: 'workflow' | 'platform';
  workflowId?: string | null;
  evaluatedAt?: string | null;
}

export interface Availability {
  available: boolean;
  reasonCode?: string | null;
  details?: Record<string, unknown>;
  /** No entry was returned for this action at all — treat as unavailable, never assume. */
  unknown: boolean;
}

export function allowedActionsUrl(workflowId?: string): string {
  return workflowId ? `/workflows/${encodeURIComponent(workflowId)}/allowed-actions` : '/allowed-actions';
}

export function fetchAllowedActions(workflowId?: string): Promise<AllowedActionsView> {
  return apiGet<AllowedActionsView>(allowedActionsUrl(workflowId));
}

export type ActionIndex = Map<string, ActionEntry>;

function key(action: string, targetId?: string | null): string {
  return `${action}::${targetId ?? ''}`;
}

export function indexActions(view: AllowedActionsView | undefined): ActionIndex {
  const idx: ActionIndex = new Map();
  for (const entry of view?.allowed ?? []) idx.set(key(entry.action, entry.targetId), entry);
  for (const entry of view?.unavailable ?? []) idx.set(key(entry.action, entry.targetId), entry);
  return idx;
}

export function resolveAvailability(idx: ActionIndex, action: string, targetId?: string | null): Availability {
  const entry = idx.get(key(action, targetId)) ?? idx.get(key(action, null));
  if (!entry) return { available: false, unknown: true };
  return { available: entry.available, reasonCode: entry.reasonCode ?? null, details: entry.details, unknown: false };
}
