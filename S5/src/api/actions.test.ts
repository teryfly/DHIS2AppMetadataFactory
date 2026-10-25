/*
 * The availability rule (UI-BR-01/02, UI-WB-006): a target+action is enabled only when S1 says so;
 * anything not reported is disabled rather than guessed. These map directly to the acceptance
 * criteria AC-01 (operator) and AC-02 (COMMIT absent from allowed-actions).
 */
import { describe, it, expect } from 'vitest';
import { indexActions, resolveAvailability, ACTION } from './actions';
import type { AllowedActionsView } from './types';

const view: AllowedActionsView = {
  workflowId: 'WF-1',
  scope: 'workflow',
  allowed: [
    { action: ACTION.executionCommit, available: true, targetId: 'RUN-2' },
    { action: ACTION.stageRun, available: true, targetId: 'ST-domain' },
  ],
  unavailable: [
    { action: ACTION.gateApprove, available: false, targetId: 'GT-1', reasonCode: 'WARNING_UNRESOLVED' },
  ],
};

describe('allowed-actions availability', () => {
  const index = indexActions(view);

  it('reports available for a listed allowed action+target', () => {
    expect(resolveAvailability(index, ACTION.executionCommit, 'RUN-2')).toMatchObject({ available: true, unknown: false });
  });

  it('reports unavailable with the S1 reason for a listed unavailable action', () => {
    const a = resolveAvailability(index, ACTION.gateApprove, 'GT-1');
    expect(a).toMatchObject({ available: false, reasonCode: 'WARNING_UNRESOLVED', unknown: false });
  });

  it('is target-scoped: the same action on a different target is unknown → disabled', () => {
    expect(resolveAvailability(index, ACTION.executionCommit, 'RUN-99')).toMatchObject({ available: false, unknown: true });
  });

  it('never infers availability from a missing entry (UI-BR-02)', () => {
    expect(resolveAvailability(index, 'totally.unknown.action').available).toBe(false);
  });

  it('treats an undefined view as all-disabled', () => {
    const empty = indexActions(undefined);
    expect(resolveAvailability(empty, ACTION.stageRun, 'ST-x').unknown).toBe(true);
  });
});
