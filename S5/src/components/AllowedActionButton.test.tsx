/*
 * The single most important invariant: button availability is decided ONLY by allowed-actions plus
 * the operator presence — never inferred (UI-WB-006, UI-OP-002, UI-BR-01, AC-02, AC-08).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@/i18n';
import { AllowedActionButton, actionReasonText } from './AllowedActionButton';
import { useOperatorStore } from '@/stores/operator';
import { qk } from '@/api/queryKeys';
import { WORKFLOW_ID, allowedActions1 } from '@/mocks/db';

function renderButton(action: string, targetId?: string) {
  // Seed the allowed-actions cache so availability resolves synchronously (no fetch timing).
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.allowedActions(WORKFLOW_ID), allowedActions1);
  return render(
    <QueryClientProvider client={qc}>
      <AllowedActionButton workflowId={WORKFLOW_ID} action={action} targetId={targetId} onExecute={() => {}}>
        do-it
      </AllowedActionButton>
    </QueryClientProvider>,
  );
}

beforeEach(() => useOperatorStore.getState().setName('Ana'));
afterEach(() => {
  cleanup();
  useOperatorStore.getState().clear();
});

describe('AllowedActionButton', () => {
  it('enables an action S1 lists as available once an operator is set', () => {
    renderButton('stage.run', 'ST-domain');
    const btn = screen.getByRole('button', { name: 'do-it' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it('disables every control when no operator is set (UI-OP-002 / AC-01)', () => {
    useOperatorStore.getState().clear();
    renderButton('stage.run', 'ST-domain');
    const btn = screen.getByRole('button', { name: 'do-it' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('disables an action S1 reports unavailable, e.g. COMMIT before approval (AC-02)', () => {
    renderButton('execution.commit', 'RUN-1');
    const btn = screen.getByRole('button', { name: 'do-it' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('treats an unknown action as disabled rather than guessing (UI-BR-01/02)', () => {
    renderButton('not.a.known.action');
    const btn = screen.getByRole('button', { name: 'do-it' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('maps an S1 reason code to a localised availability message', () => {
    const t = ((key: string) => key) as never;
    expect(actionReasonText(t, { available: false, unknown: false, reasonCode: 'WARNING_UNRESOLVED' })).toBe('actionReason.WARNING_UNRESOLVED');
    expect(actionReasonText(t, { available: false, unknown: true })).toBe('actionReason.unknown');
  });
});
