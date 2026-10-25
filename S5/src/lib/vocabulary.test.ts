/*
 * The status and category tokens the server sends are the factory's own shorthand. `S5M_移动优先前端_SRS.md`
 * §2.1 fixes one screen word for each of them, in both languages, and every render site reaches those
 * words through `src/lib/labels.ts` or `StatusBadge`.
 *
 * That table is the part of the promise this file can check: a missing word is invisible to the en/zh
 * parity test (a hole in both languages is still a hole in both) and invisible to TypeScript, and what
 * it costs on screen is `DESIGN_APPROVED` where a person expected to read how far a project had got.
 * So the token list is written out here — the same list as the SRS table — and a new enum value has to
 * be added to both, which is the point.
 */
import { describe, it, expect } from 'vitest';
import { en, zhCN } from '@/i18n/messages';

type Vocabulary = { block: string; tokens: string[]; exact?: boolean };

const STATUS_TOKENS = [
  // StageStatus
  'READY', 'RUNNING', 'WAITING_INPUT', 'WAITING_APPROVAL', 'PASSED', 'FAILED', 'BLOCKED', 'INVALIDATED',
  'SKIPPED', 'MANUAL_CHECK', 'LOCKED',
  // WorkflowStatus
  'ACTIVE', 'WAITING_USER', 'COMPLETED', 'PAUSED', 'CANCELLED',
  // RunStatus
  'CREATED', 'IN_PROGRESS', 'COMMITTED', 'VERIFIED',
  // GateStatus
  'WAITING', 'APPROVED', 'REJECTED',
  // ChangeRequestStatus
  'DRAFT', 'ANALYZED',
  // ExecutionStatus
  'NEEDS_MANUAL_CHECK', 'SUCCEEDED', 'VALIDATED',
  // QuestionStatus
  'OPEN', 'ANSWERED', 'WITHDRAWN', 'SUPERSEDED',
  // the console's own states, which are shown by the same chip
  'PENDING', 'DISABLED', 'COMMITTING', 'OUTDATED',
];

const TABLE: Vocabulary[] = [
  { block: 'stageStatus', tokens: STATUS_TOKENS },
  {
    block: 'roles',
    tokens: ['SOLUTION_ARCHITECT', 'DHIS2_IMPLEMENTER', 'FRONTEND_DEVELOPER', 'BUSINESS_ANALYST', 'DEPLOYMENT_APPROVER', 'PLATFORM_ADMINISTRATOR'],
    exact: true,
  },
  {
    block: 'milestone',
    tokens: ['DRAFT', 'DESIGN_APPROVED', 'PACKAGE_APPROVED', 'DHIS2_VALIDATED', 'DEPLOYED', 'VERIFIED', 'HANDED_OFF'],
    exact: true,
  },
  { block: 'disposition', tokens: ['CREATE', 'UPDATE', 'DELETE', 'REUSE', 'REFERENCE', 'EXTERNAL', 'DEPRECATED'], exact: true },
  { block: 'refType', tokens: ['ARTIFACT', 'BLUEPRINT_DRAFT', 'BLUEPRINT_VERSION', 'ENVIRONMENT', 'EXECUTION'], exact: true },
  { block: 'severity', tokens: ['BLOCKING', 'ERROR', 'WARNING', 'INFO', 'HIGH', 'MEDIUM', 'LOW'], exact: true },
  { block: 'nrrPriority', tokens: ['MUST', 'SHOULD', 'COULD', 'WONT'], exact: true },
  { block: 'nrrStatus', tokens: ['CANDIDATE', 'CONFIRMED', 'REJECTED', 'NEEDS_REVIEW'], exact: true },
  {
    block: 'runStatus',
    tokens: [
      'READY', 'PENDING', 'RUNNING', 'WAITING_INPUT', 'WAITING_APPROVAL', 'COMPLETED', 'PASSED',
      'FAILED', 'BLOCKED', 'MANUAL_CHECK', 'POLICY_DENIED', 'BUDGET_EXCEEDED', 'CANCELLED',
    ],
    exact: true,
  },
  {
    block: 'nrrCategory',
    tokens: [
      'businessGoal', 'participant', 'businessEntity', 'useCase', 'businessProcess', 'businessRule',
      'dataRequirement', 'organisationRequirement', 'accessRequirement', 'reportRequirement',
      'notificationRequirement', 'i18nRequirement', 'openQuestion',
    ],
    exact: true,
  },
  { block: 'budgetState', tokens: ['UNSET', 'OK', 'WARNING', 'EXCEEDED'], exact: true },
  { block: 'handoffKind', tokens: ['DOCUMENT', 'CONTRACT', 'UI_SPEC'], exact: true },
  { block: 'waiver', tokens: ['NON_PRODUCTION_VERIFIED'], exact: true },
  { block: 'freshness', tokens: ['VALID', 'EXPIRED', 'VERSION_CHANGED'], exact: true },
  { block: 'identitySource', tokens: ['local', 'oidc'], exact: true },
  { block: 'diffState', tokens: ['ADDED', 'UPDATED', 'CREATED', 'UNCHANGED', 'REMOVED', 'REFERENCE_ONLY'], exact: true },
  { block: 'deletionPolicy', tokens: ['FORBID', 'SAFE_ONLY'], exact: true },
  { block: 'usageDimensions', tokens: ['stage', 'agent', 'model'], exact: true },
  { block: 'notificationChannel', tokens: ['IN_APP', 'EMAIL', 'WEBHOOK', 'WEBHOOK_SECRET'], exact: true },
  {
    block: 'artifactKinds',
    tokens: [
      'BUSINESS_INPUT', 'NRR', 'METADATA_PACKAGE', 'METADATA_BASELINE', 'BLUEPRINT', 'BLUEPRINT_EXPORT', 'ENV_PACKAGE',
      'DATASTORE_CHANGESET', 'OPENAPI', 'UI_SPECIFICATION', 'FRONTEND_PACKAGE', 'FIXTURE_PACKAGE', 'VALIDATION_REPORT',
      'EXECUTION_REPORT', 'IMPORT_REPORT', 'POST_VERIFY_REPORT', 'DIFF_REPORT', 'CHANGE_IMPACT_REPORT', 'SNAPSHOT',
      'PROJECT_EXPORT', 'MANIFEST', 'HANDOFF_CHECK_REPORT',
    ],
    exact: true,
  },
  { block: 'inputs.classes', tokens: ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'PHI_PII'] },
  // `default` is this console's fallback row, not a token; the rest are NotificationType as the server spells it.
  {
    block: 'notifications.types',
    tokens: [
      'GATE_PENDING', 'GATE_REMINDER', 'GATE_APPROVED', 'GATE_REJECTED', 'GATE_INVALIDATED', 'QUESTION_CREATED',
      'STAGE_FAILED', 'STAGE_BLOCKED', 'STAGE_MANUAL_CHECK', 'EXECUTION_COMPLETED', 'EXECUTION_FAILED',
      'BUDGET_THRESHOLD', 'BUDGET_EXCEEDED', 'CREDENTIAL_EXPIRING', 'AUDIT_INTEGRITY_FAILED',
    ],
    exact: true,
  },
];

function entriesAt(locale: unknown, path: string): Record<string, string> | undefined {
  const value = path.split('.').reduce<unknown>(
    (acc, key) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[key] : undefined),
    locale,
  );
  return value && typeof value === 'object' ? (value as Record<string, string>) : undefined;
}

describe('the on-screen value vocabulary', () => {
  for (const { block, tokens, exact } of TABLE) {
    it(`${block} has a word for every token, in both languages`, () => {
      const enWords = entriesAt(en, block);
      const zhWords = entriesAt(zhCN, block);
      expect(enWords, `en has no "${block}" block`).toBeTypeOf('object');
      expect(zhWords, `zh has no "${block}" block`).toBeTypeOf('object');
      const missing: string[] = [];
      for (const token of tokens) {
        const enWord = enWords?.[token];
        const zhWord = zhWords?.[token];
        // A word that is the token again is a hole that reads as a translation: the status chip's
        // `defaultValue` fallback already prints the token, so this file has to catch it too.
        if (typeof enWord !== 'string' || enWord.length === 0 || enWord === token) missing.push(`en ${block}.${token}`);
        if (typeof zhWord !== 'string' || zhWord.length === 0) missing.push(`zh ${block}.${token}`);
      }
      expect(missing).toEqual([]);
    });

    if (exact) {
      it(`${block} carries no word the server cannot send`, () => {
        const enWords = entriesAt(en, block);
        const extra = Object.keys(enWords ?? {}).filter((key) => key !== 'default' && !tokens.includes(key));
        expect(extra).toEqual([]);
      });
    }
  }
});
