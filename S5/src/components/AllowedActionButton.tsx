/*
 * UI-WB-006 / UI-BR-01: EVERY state-changing button routes through here. A button is enabled only
 * when (a) an operator name is set (UI-OP-002) and (b) S1's allowed-actions lists the action as
 * available for this target. When disabled the S1 reason (or the missing-operator hint) is shown —
 * the UI never decides availability itself.
 *
 * UI-REQ-003: on a 409 or APPROVAL_INVALIDATED it refreshes allowed-actions (and the workflow)
 * before the toast surfaces the reason, because server state has moved.
 */
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Button, Tooltip } from '@dhis2/ui';
import { useQueryClient } from '@tanstack/react-query';
import { useAllowedActions, type Availability } from '@/api/actions';
import { isApiError } from '@/api/errors';
import { qk } from '@/api/queryKeys';
import { hasOperator } from '@/stores/operator';

type Tone = 'primary' | 'secondary' | 'destructive';

interface Props {
  workflowId: string | undefined;
  /** `platform` reads GET /allowed-actions for the Administration pages (FR-UI-009). */
  scope?: 'workflow' | 'platform';
  action: string;
  targetId?: string;
  onExecute: () => Promise<unknown> | void;
  children: ReactNode;
  tone?: Tone;
  loading?: boolean;
  /** Extra UI-side disable (e.g. unhandled warnings) layered ON TOP of allowed-actions; never relaxes it. */
  disabled?: boolean;
}

/** Localise an unavailable reason; falls back to a generic line if S1 sends an unknown code. */
export function actionReasonText(t: TFunction, availability: Availability): string {
  if (availability.unknown) return t('actionReason.unknown');
  if (availability.reasonCode) {
    return t(`actionReason.${availability.reasonCode}`, { defaultValue: t('actionReason.unknown') });
  }
  return t('actionReason.unknown');
}

export function AllowedActionButton({ workflowId, scope = 'workflow', action, targetId, onExecute, children, tone = 'primary', loading, disabled: extraDisabled }: Props) {
  const { t } = useTranslation();
  const { check } = useAllowedActions(workflowId, scope);
  const queryClient = useQueryClient();
  const operator = hasOperator();
  const availability = check(action, targetId);

  const disabled = !operator || !availability.available || !!loading || !!extraDisabled;
  const reason = !operator ? t('operator.missing') : availability.available ? '' : actionReasonText(t, availability);

  const handleClick = async () => {
    try {
      await onExecute();
    } catch (error) {
      if (isApiError(error) && (error.isConflict || error.isApprovalInvalidated) && workflowId) {
        // Refresh server truth before the toast (UI-REQ-003). The mutation cache raised the toast.
        void queryClient.invalidateQueries({ queryKey: qk.allowedActions(workflowId) });
        void queryClient.invalidateQueries({ queryKey: qk.workflow(workflowId) });
      }
    }
  };

  const button = (
    <Button
      primary={tone === 'primary'}
      secondary={tone === 'secondary'}
      destructive={tone === 'destructive'}
      disabled={disabled}
      loading={loading}
      onClick={handleClick}
    >
      {children}
    </Button>
  );

  return reason ? (
    <Tooltip content={reason} openDelay={150} placement="top">
      <span style={{ display: 'inline-block' }}>{button}</span>
    </Tooltip>
  ) : (
    button
  );
}
