import { useTranslation } from 'react-i18next';
import { Tag } from '@dhis2/ui';

type Skin = 'default' | 'neutral' | 'positive' | 'negative';

const STATUS_SKIN: Record<string, Skin> = {
  PENDING: 'default',
  CREATED: 'default',
  IN_PROGRESS: 'default',
  RUNNING: 'neutral',
  WAITING_INPUT: 'neutral',
  WAITING_APPROVAL: 'neutral',
  MANUAL_CHECK: 'neutral',
  VALIDATED: 'positive',
  PASSED: 'positive',
  VERIFIED: 'positive',
  APPROVED: 'positive',
  ACTIVE: 'positive',
  COMPLETED: 'positive',
  SUCCEEDED: 'positive',
  FAILED: 'negative',
  BLOCKED: 'negative',
  INVALIDATED: 'negative',
  REJECTED: 'negative',
  LOCKED: 'negative',
  OUTDATED: 'neutral',
  COMMITTING: 'neutral',
  CANCELLED: 'default',
  SKIPPED: 'default',
};

/** Localised status chip; text always comes from stageStatus.* so there is no hardcoded language. */
export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const { t } = useTranslation();
  const skin = STATUS_SKIN[status] ?? 'default';
  // `label` lets a page that knows which enum it is holding override the one shared word — `ACTIVE`
  // reads as 启用中 for an environment and as 进行中 for a project's workflow.
  const text = label ?? t(`stageStatus.${status}`, { defaultValue: status });
  return (
    <Tag neutral={skin === 'neutral'} positive={skin === 'positive'} negative={skin === 'negative'}>
      {text}
    </Tag>
  );
}
