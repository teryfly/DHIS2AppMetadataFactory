/*
 * SRS §2.1 fixes one screen word for every status, role and category token the factory puts on the
 * wire. `src/lib/labels.ts` reads those words, and this file is what stops the table from rotting:
 * a token that has no word prints as the token itself — vue-i18n has no `defaultValue`, so the
 * fallback lives in `labels.ts` and can only be checked against the key sets below.
 *
 * The lists are the SRS table, written out. Adding an enum value on the server means adding it here
 * and in both locale files, which is the point: the alternative is an operator reading
 * `DESIGN_APPROVED` on the home screen and guessing what stage that is.
 */
import { describe, it, expect } from 'vitest';
import en from '@/i18n/locales/en';
import zh from '@/i18n/locales/zh-CN';

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
  // ExecutionStatus, and the two verdicts the run console can be handed
  'NEEDS_MANUAL_CHECK', 'SUCCEEDED', 'VALIDATED',
  // QuestionStatus
  'OPEN', 'ANSWERED', 'WITHDRAWN', 'SUPERSEDED',
  // the console's own states, shown by the same tag
  'PENDING', 'DISABLED', 'COMMITTING', 'OUTDATED',
];

const TABLE: Vocabulary[] = [
  { block: 'stageStatus', tokens: STATUS_TOKENS },
  { block: 'stageTypes', tokens: ['REQUIREMENTS', 'DOMAIN', 'DISCOVERY', 'MAPPING', 'PAGE_DESIGN', 'BASELINE_COMPILE', 'BIND_COMPILE', 'STATIC_VALIDATION', 'DHIS2_VALIDATE', 'COMMIT', 'POST_VERIFY', 'FIXTURE_IMPORT', 'HANDOFF'], exact: true },
  { block: 'severity', tokens: ['BLOCKING', 'ERROR', 'WARNING', 'INFO', 'HIGH', 'MEDIUM', 'LOW'], exact: true },
  { block: 'roles', tokens: ['SOLUTION_ARCHITECT', 'DHIS2_IMPLEMENTER', 'FRONTEND_DEVELOPER', 'BUSINESS_ANALYST', 'DEPLOYMENT_APPROVER', 'PLATFORM_ADMINISTRATOR'], exact: true },
  { block: 'milestone', tokens: ['DRAFT', 'DESIGN_APPROVED', 'PACKAGE_APPROVED', 'DHIS2_VALIDATED', 'DEPLOYED', 'VERIFIED', 'HANDED_OFF'], exact: true },
  { block: 'disposition', tokens: ['CREATE', 'UPDATE', 'DELETE', 'REUSE', 'REFERENCE', 'EXTERNAL', 'DEPRECATED'], exact: true },
  { block: 'refType', tokens: ['ARTIFACT', 'BLUEPRINT_DRAFT', 'BLUEPRINT_VERSION', 'ENVIRONMENT', 'EXECUTION'], exact: true },
  { block: 'nrrPriority', tokens: ['MUST', 'SHOULD', 'COULD', 'WONT'], exact: true },
  { block: 'nrrStatus', tokens: ['CONFIRMED', 'NEEDS_REVIEW'], exact: true },
  {
    block: 'runStatus',
    tokens: [
      'READY', 'PENDING', 'RUNNING', 'WAITING_INPUT', 'WAITING_APPROVAL', 'COMPLETED', 'PASSED',
      'FAILED', 'BLOCKED', 'MANUAL_CHECK', 'POLICY_DENIED', 'BUDGET_EXCEEDED', 'CANCELLED',
    ],
    exact: true,
  },
  { block: 'entryStatus', tokens: ['CANDIDATE', 'CONFIRMED', 'REJECTED'], exact: true },
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

describe('the on-screen value vocabulary (UM-AC-16)', () => {
  for (const { block, tokens, exact } of TABLE) {
    it(`${block} has a word for every token, in both languages`, () => {
      const enWords = entriesAt(en, block);
      const zhWords = entriesAt(zh, block);
      expect(enWords, `en has no "${block}" block`).toBeTypeOf('object');
      expect(zhWords, `zh has no "${block}" block`).toBeTypeOf('object');
      const missing: string[] = [];
      for (const token of tokens) {
        const enWord = enWords?.[token];
        const zhWord = zhWords?.[token];
        // a word that repeats the token is a hole that looks like a translation
        if (typeof enWord !== 'string' || enWord.length === 0 || enWord === token) missing.push(`en ${block}.${token}`);
        if (typeof zhWord !== 'string' || zhWord.length === 0) missing.push(`zh ${block}.${token}`);
      }
      expect(missing).toEqual([]);
    });

    if (exact) {
      it(`${block} carries no word the server cannot send`, () => {
        const extra = Object.keys(entriesAt(en, block) ?? {}).filter((key) => key !== 'default' && !tokens.includes(key));
        expect(extra).toEqual([]);
      });
    }
  }

  it('words the parse and classification lookups read', () => {
    // `labels.ts` maps these tokens onto the flat keys the upload form already had, so the mapping
    // breaks the moment a key is renamed — which is exactly what a test has to notice.
    for (const key of ['classPublic', 'classInternal', 'classConfidential', 'classRestricted', 'classPhi']) {
      expect((en.inputs as Record<string, string>)[key], `en inputs.${key}`).toBeTruthy();
      expect((zh.inputs as Record<string, string>)[key], `zh inputs.${key}`).toBeTruthy();
    }
    for (const key of ['parseNone', 'parsed', 'needsReview', 'parseFailed', 'policyDenied']) {
      expect((en.inputs as Record<string, string>)[key], `en inputs.${key}`).toBeTruthy();
      expect((zh.inputs as Record<string, string>)[key], `zh inputs.${key}`).toBeTruthy();
    }
  });
});
