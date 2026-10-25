/*
 * UI-WB-006 / UI-BR-01: a button is enabled ONLY if S1's `allowed-actions` lists its action as
 * available for the current target; when disabled we show S1's reason. This module is the single
 * place the UI reads availability. It NEVER infers availability locally.
 */
import { useQuery, queryOptions } from '@tanstack/react-query';
import { client, unwrap } from './client';
import { qk } from './queryKeys';
import type { AllowedActionsView, ActionEntry } from './types';

/**
 * Action identifiers, mirroring S1's catalog. Referencing these by constant keeps button wiring
 * honest: if S1 renames an action the availability simply becomes "unknown → disabled", never "guessed".
 */
export const ACTION = {
  // Emitted by S1's platform `allowed-actions` since 1.6.0 (S5IF-SET01 §12.3). `POST /projects`
  // guards itself with this very name, so a console that cannot ask has no honest way to draw the
  // button — and a missing entry still means "unavailable", never "allowed" (UI-BR-01).
  projectCreate: 'project.create',
  secretManage: 'secret.manage',
  projectEdit: 'project.edit',
  projectArchive: 'project.archive',
  projectDelete: 'project.delete',
  projectExport: 'project.export',
  // S1 emits this one; the table simply never carried it, so no control could gate on an import.
  projectImport: 'project.import',
  projectUpgradeMode: 'project.upgrade_mode',
  inputUpload: 'input.upload',
  inputParse: 'input.parse',
  inputConfirmColumnMapping: 'input.confirm_column_mapping',
  artifactExplain: 'artifact.explain',
  artifactDownload: 'artifact.download',
  // CP-UI-021: S1 answers this with `PACKAGE_NOT_GENERATED` until the HANDOFF stage
  // delivers a FRONTEND_PACKAGE. It is the only thing that says whether the development
  // package exists, so the Handoff page asks it rather than guessing from a 404 (UI-BR-01).
  frontendPackageDownload: 'frontend_package.download',
  deploymentCreate: 'deployment.create',
  deploymentCancel: 'deployment.cancel',
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
  environmentTestConnection: 'environment.test_connection',
  environmentProbeCapabilities: 'environment.probe_capabilities',
  environmentEmergencyLock: 'environment.emergency_lock',
  governanceModelRegister: 'governance.model.register',
  governanceModelEnable: 'governance.model.enable',
  governancePromptActivate: 'governance.prompt.activate',
  governancePolicyRelax: 'governance.policy.relax',
  governanceProviderRegister: 'governance.provider.register',
  governanceProviderEnable: 'governance.provider.enable',
  // Both emitted by S1 today: `stage.chat` by the stage action list (domain/actions.py), the budget
  // write by the platform list.  No invented keys — UI-BR-01 allows only what S1 reports.
  stageChat: 'stage.chat',
  // The four M4 Administration rights. These three strings are S1's own (`iam.py:91-93`, listed in
  // `domain/actions.py`), and the earlier spellings here — `project.member.manage`,
  // `artifact.grant.manage`, `production.exemption.manage` — matched nothing S1 emits, so every
  // button gated on them sat permanently disabled showing "S1 gave no reason" even to a platform
  // administrator. A key that is not in the server's answer means unavailable, not "probably fine".
  userManage: 'user.manage',
  projectMemberManage: 'member.manage',
  artifactGrantManage: 'restricted_grant.manage',
  productionExemptionManage: 'production_exemption.manage',
  governanceBudgetSet: 'governance.budget.set',
  // The system-settings write (`PUT /settings`) was gated on nothing but "an operator is typed in",
  // which is the UI deciding availability for itself (UI-BR-01).
  settingsManage: 'settings.manage',
} as const;

export type ActionName = (typeof ACTION)[keyof typeof ACTION];

/** One resolved availability decision for a (action, targetId) pair. */
export interface Availability {
  available: boolean;
  reasonCode?: string;
  details?: Record<string, unknown>;
  /** True when S1 returned no entry at all → treat as disabled (never guess, UI-BR-01/02). */
  unknown: boolean;
}

const UNKNOWN: Availability = { available: false, unknown: true };

function keyFor(action: string, targetId?: string): string {
  return `${action}::${targetId ?? ''}`;
}

/** Build the action+target → entry lookup from an allowed-actions view (pure, testable). */
export function indexActions(view: AllowedActionsView | undefined): Map<string, ActionEntry> {
  const map = new Map<string, ActionEntry>();
  if (!view) return map;
  for (const entry of [...(view.allowed ?? []), ...(view.unavailable ?? [])]) {
    // A target may have several entries (per stage / gate / run / env); key by action+targetId.
    map.set(keyFor(entry.action, entry.targetId ?? undefined), entry);
  }
  return map;
}

/** Resolve availability for (action, target). Unknown → disabled; never inferred (UI-BR-01/02). */
export function resolveAvailability(index: Map<string, ActionEntry>, action: string, targetId?: string): Availability {
  const entry = index.get(keyFor(action, targetId));
  if (!entry) return UNKNOWN;
  return {
    available: entry.available,
    reasonCode: entry.reasonCode ?? undefined,
    details: entry.details as Record<string, unknown> | undefined,
    unknown: false,
  };
}

/**
 * FR-UI-009 for the Administration pages, which belong to no workflow: `GET /allowed-actions`
 * answers the same entry shape for environment, governance and platform-level actions
 * (`scope: 'platform'`). Without it those pages had no source and had to decide locally who may
 * flip a production emergency lock — the one thing UI-BR-01 forbids.
 */
export function platformActionsOptions() {
  return queryOptions({
    queryKey: qk.platformAllowedActions,
    queryFn: () => unwrap<AllowedActionsView>(() => client.GET('/api/v1/allowed-actions', {})),
  });
}

export function allowedActionsOptions(workflowId: string | undefined) {
  return queryOptions({
    queryKey: qk.allowedActions(workflowId ?? '-'),
    enabled: !!workflowId,
    queryFn: () =>
      unwrap<AllowedActionsView>(() =>
        client.GET('/api/v1/workflows/{workflow_id}/allowed-actions', {
          params: { path: { workflow_id: workflowId! } },
        }),
      ),
  });
}

/**
 * Hook returning a `check(action, targetId?)` selector plus the raw view. Consumers disable a
 * button when `!check(...).available` and render the reason via `useActionReason(...)`.
 */
export function useAllowedActions(workflowId: string | undefined, scope: 'workflow' | 'platform' = 'workflow') {
  // One mechanism for both answers: S1 shapes the entries identically, only the source differs
  // (`/workflows/{id}/allowed-actions` versus `/allowed-actions` for the platform pages).
  const platform = scope === 'platform';
  const query = useQuery<AllowedActionsView>({
    queryKey: platform ? qk.platformAllowedActions : qk.allowedActions(workflowId ?? '-'),
    enabled: platform || !!workflowId,
    queryFn: () =>
      platform
        ? unwrap<AllowedActionsView>(() => client.GET('/api/v1/allowed-actions', {}))
        : unwrap<AllowedActionsView>(() =>
            client.GET('/api/v1/workflows/{workflow_id}/allowed-actions', {
              params: { path: { workflow_id: workflowId ?? '-' } },
            }),
          ),
  });
  const byKey = indexActions(query.data);

  const check = (action: string, targetId?: string): Availability => resolveAvailability(byKey, action, targetId);

  return { check, view: query.data, isLoading: query.isLoading, isError: query.isError };
}
