import { i18n } from '@/i18n';
import type { TimelineItem } from '@/fui/types';

const t = (k: string, named?: Record<string, string | number>): string => String(named ? i18n.global.t(k, named) : i18n.global.t(k));

export type Tone = 'success' | 'danger' | 'warning' | 'info' | 'muted' | 'primary';

/**
 * One word out of a token→word block (`stageStatus`, `roles`, `artifactKinds`, …), which is keyed by
 * the server's own value.
 *
 * vue-i18n has no `defaultValue`: a key it does not know prints the key. So the lookup asks `te()`
 * first and shows the raw token when there is no word — the operator reads a strange uppercase word
 * and knows this console has not heard of it, which is truer than a path or a guess. The token list
 * these blocks must cover is asserted by `src/lib/vocabulary.test.ts`.
 */
const word = (block: string, token?: string | null): string => {
  if (!token) return '';
  const key = `${block}.${token}`;
  return i18n.global.te(key) ? t(key) : token;
};

export function stageStatusLabel(status: string): string {
  return word('stageStatus', status);
}

/** A stage or agent-run status as the run-log sheet names it. */
export function runStatusLabel(status?: string | null): string {
  return word('runStatus', status);
}

/** Gate badge words — the server token is not an operator word (UM-AC-16). */
export function gateStatusLabel(status: string): string {
  switch (status) {
    case 'WAITING_APPROVAL':
      return t('gate.statusWaiting');
    case 'APPROVED':
      return t('gate.approved');
    case 'REJECTED':
      return t('gate.rejected');
    case 'INVALIDATED':
      return t('gate.statusInvalidated');
    default:
      return status;
  }
}

/** One recorded approval decision as the approver reads it. */
export function decisionLabel(decision?: string | null): string {
  switch (decision) {
    case 'APPROVED':
      return t('gate.approved');
    case 'REJECTED':
      return t('gate.rejected');
    default:
      return decision ?? '';
  }
}

export function stageTypeLabel(type: string): string {
  return word('stageTypes', type);
}

/**
 * Which gate this is, in the words the operator already knows for the work it closes.
 *
 * A gate is named after the stage it stops (`S1/.../domain/enums.py:_GATE_STAGE`), so the seven gate
 * types reuse the stage words instead of adding a second vocabulary. The pairs are written out rather
 * than derived: `ARCHITECTURE_GATE` closes MAPPING and `PACKAGE_GATE` closes STATIC_VALIDATION, so
 * reading the token would get both wrong. Unknown tokens stay as sent — a blank row would read as
 * "not a gate" rather than "a gate this console has not heard of".
 */
export function gateTypeLabel(type?: string | null): string {
  if (!type) return '';
  switch (type) {
    case 'REQUIREMENT_GATE':
      return t('stageTypes.REQUIREMENTS');
    case 'DOMAIN_GATE':
      return t('stageTypes.DOMAIN');
    case 'ARCHITECTURE_GATE':
      return t('stageTypes.MAPPING');
    case 'DESIGN_GATE':
      return t('stageTypes.PAGE_DESIGN');
    case 'PACKAGE_GATE':
      return t('stageTypes.STATIC_VALIDATION');
    case 'DEPLOY_GATE':
      return t('stageTypes.COMMIT');
    case 'HANDOFF_GATE':
      return t('stageTypes.HANDOFF');
    default:
      return type;
  }
}

export function toneForStageStatus(status: string): Tone {
  switch (status) {
    case 'PASSED':
      return 'success';
    case 'FAILED':
    case 'BLOCKED':
    case 'INVALIDATED':
      return 'danger';
    case 'WAITING_APPROVAL':
    case 'WAITING_INPUT':
    case 'MANUAL_CHECK':
      return 'warning';
    case 'RUNNING':
    case 'IN_PROGRESS':
      return 'primary';
    default:
      return 'muted';
  }
}

export function toTimelineStatus(status: string): TimelineItem['status'] {
  switch (status) {
    case 'PASSED':
      return 'done';
    case 'FAILED':
      return 'failed';
    case 'BLOCKED':
    case 'INVALIDATED':
      return 'blocked';
    case 'SKIPPED':
      return 'skipped';
    case 'RUNNING':
    case 'IN_PROGRESS':
    case 'WAITING_APPROVAL':
    case 'WAITING_INPUT':
    case 'MANUAL_CHECK':
      return 'current';
    default:
      return 'pending';
  }
}

export function severityLabel(sev: string): string {
  return word('severity', sev);
}

export function toneForSeverity(sev: string): Tone {
  switch (sev) {
    case 'BLOCKING':
    case 'ERROR':
    case 'HIGH':
      return 'danger';
    case 'WARNING':
    case 'MEDIUM':
      return 'warning';
    default:
      return 'info';
  }
}

export function toneForRunStatus(status: string): Tone {
  switch (status) {
    case 'COMMITTED':
    case 'SUCCEEDED':
    case 'VERIFIED':
      return 'success';
    case 'FAILED':
    case 'CANCELLED':
      return 'danger';
    case 'OUTDATED':
      return 'muted';
    default:
      return 'warning';
  }
}

export function toneForGateStatus(status: string): Tone {
  switch (status) {
    case 'APPROVED':
      return 'success';
    case 'REJECTED':
      return 'danger';
    case 'INVALIDATED':
      return 'muted';
    default:
      return 'warning';
  }
}

/** Environment class label. `UAT` is what S1 accepts where this map once said `STAGING`. */
export function envClassLabel(cls?: string | null): string {
  if (!cls) return '';
  const map: Record<string, string> = { DEV: 'classDEV', TEST: 'classTEST', UAT: 'classUAT', PRODUCTION: 'classPROD' };
  return map[cls] ? t(`environments.${map[cls]}`) : cls;
}

/**
 * Provider class label, from the one copy of this map in the app.
 *
 * It used to exist twice — the governance table and the provider sheet each kept a `CLASS_LABEL`,
 * and the sheet's lookup passed i18next's `{ defaultValue }` option to vue-i18n, which does not
 * read it: an unknown class rendered as a blank line instead of the raw token it was meant to show.
 * A stored provider whose class predates the current vocabulary has to stay readable.
 */
/**
 * Which delivery line a project is on, and what kind of work it was started from.
 *
 * The wire carries `A`/`B` and `GREENFIELD`/`BROWNFIELD`. Those are S1's shorthand, not words: the
 * delivery mode decides who designs the pages and therefore which stages exist at all, so a project
 * card that shows a bare letter is a card you cannot read. Both pairs reuse the wording the new
 * project offers for the same choice, so one thing keeps one name.
 */
export function deliveryModeLabel(mode?: string | null): string {
  switch (mode) {
    case 'A':
      return t('projects.modeA');
    case 'B':
      return t('projects.modeB');
    default:
      return mode ?? '';
  }
}

export function projectTypeLabel(kind?: string | null): string {
  switch (kind) {
    case 'GREENFIELD':
      return t('projects.greenfield');
    case 'BROWNFIELD':
      return t('projects.brownfield');
    default:
      return kind ?? '';
  }
}

export function providerClassLabel(value?: string | null): string {
  if (!value) return '';
  const map: Record<string, string> = {
    LOCAL_ONLY: 'governance.classLocal',
    PRIVATE_NETWORK: 'governance.classPrivate',
    APPROVED_EXTERNAL: 'governance.classApproved',
    UNRESTRICTED: 'governance.classUnrestricted',
  };
  return map[value] ? t(map[value]) : value;
}

/**
 * Capability backend name. `/setup` reports its three wiring keys as `S2`/`S3`/`S4` and an artifact
 * row carries the subsystem code that created it; neither is a word the operator can act on, so
 * every render site comes through here. An unknown code returns the raw token — the `providers` map
 * is `{[key: string]: string}` in the contract, and a blank line would read as "no capability".
 */
export function capabilityLabel(code?: string | null): string {
  if (!code) return '';
  switch (code) {
    case 'S2':
      return t('capabilities.blueprint');
    case 'S3':
      return t('capabilities.parsing');
    case 'S4':
      return t('capabilities.execution');
    default:
      return code;
  }
}

/** Whether a capability reaches the real implementation or the in-process stand-in. */
export function modeLabel(mode?: string | null): string {
  if (!mode) return '';
  switch (mode) {
    case 'real':
      return t('capabilities.real');
    case 'fake':
      return t('capabilities.demoStandIn');
    default:
      return mode;
  }
}

/** One `providers` entry as the operator reads it: 「需求解析：演示替身」. */
export function capabilityEntry(code: string, mode: string): string {
  return t('capabilities.item', { name: capabilityLabel(code), mode: modeLabel(mode) });
}

/**
 * The rest of the value vocabulary (SRS §2.1): nouns and states that reach a row or a chip. Each is
 * one line because the block is the lookup; `vocabulary.test.ts` is what keeps the block complete.
 */
export const roleLabel = (value?: string | null): string => word('roles', value);
export const milestoneLabel = (value?: string | null): string => word('milestone', value);
export const dispositionLabel = (value?: string | null): string => word('disposition', value);
export const refTypeLabel = (value?: string | null): string => word('refType', value);
export const nrrPriorityLabel = (value?: string | null): string => word('nrrPriority', value);
export const nrrStatusLabel = (value?: string | null): string => word('nrrStatus', value);
/**
 * A blueprint requirement entry's own state (CANDIDATE | CONFIRMED | REJECTED). Distinct from
 * `nrrStatusLabel`, which is S3's parse outcome and only has two values — the draft's third
 * (`REJECTED`) would print as the raw token if the two shared a block.
 */
export const entryStatusLabel = (value?: string | null): string => word('entryStatus', value);
export const budgetStateLabel = (value?: string | null): string => word('budgetState', value);
export const handoffKindLabel = (value?: string | null): string => word('handoffKind', value);
export const waiverLabel = (value?: string | null): string => word('waiver', value);
export const freshnessLabel = (value?: string | null): string => word('freshness', value);
export const diffStateLabel = (value?: string | null): string => word('diffState', value);
export const artifactKindLabel = (value?: string | null): string => word('artifactKinds', value);
export const identitySourceLabel = (value?: string | null): string => word('identitySource', value);
export const deletionPolicyLabel = (value?: string | null): string => word('deletionPolicy', value);

/** Which way a platform notification leaves the factory. */
export const channelLabel = (value?: string | null): string => word('notificationChannel', value?.toUpperCase());

/** How far data may travel out of this deployment on its way to a model. */
export const dataPolicyLabel = (value?: string | null): string =>
  value && i18n.global.te(`environments.policy${value}`) ? t(`environments.policy${value}`) : (value ?? '');

/** Which part of an environment a credential lets the Factory touch. */
export const credentialScopeLabel = (value?: string | null): string =>
  value && i18n.global.te(`environments.scope${value.toUpperCase()}`) ? t(`environments.scope${value.toUpperCase()}`) : '';

/** How the Factory authenticates to an environment: a token, or a name and password. */
export const authModeLabel = (value?: string | null): string =>
  value && i18n.global.te(`environments.auth${value}`) ? t(`environments.auth${value}`) : (value ?? '');

/**
 * A project's workflow state. `ACTIVE` is one token for two sentences — an environment is ACTIVE when
 * nobody switched it off, a workflow is ACTIVE while it still has work going on — and the shared word
 * is the environment's, so the project asks for the running one.
 */
export function workflowStatusLabel(value?: string | null): string {
  if (!value) return '';
  return value === 'ACTIVE' ? t('stageStatus.IN_PROGRESS') : word('stageStatus', value);
}

/** Parse state of one uploaded file (`S1 rest/schemas/inputs.py`), whose words the upload form already has. */
export function parseStatusLabel(value?: string | null): string {
  if (!value) return '';
  switch (value) {
    case 'NONE':
      return t('inputs.parseNone');
    case 'PARSING':
      return t('inputs.parsing');
    case 'PARSED':
      return t('inputs.parsed');
    case 'NEEDS_REVIEW':
      return t('inputs.needsReview');
    case 'FAILED':
      return t('inputs.parseFailed');
    case 'POLICY_DENIED':
      return t('inputs.policyDenied');
    default:
      return value;
  }
}

/** How sensitive the content is; the upload form words it the same way. */
export function classificationLabel(value?: string | null): string {
  if (!value) return '';
  switch (value) {
    case 'PUBLIC':
      return t('inputs.classPublic');
    case 'INTERNAL':
      return t('inputs.classInternal');
    case 'CONFIDENTIAL':
      return t('inputs.classConfidential');
    case 'RESTRICTED':
      return t('inputs.classRestricted');
    case 'PHI_PII':
      return t('inputs.classPhi');
    default:
      return value;
  }
}

/**
 * One explanation entry as a line of text.
 *
 * The gate's `why` and `canChange` come back as small objects (`{action, target, invalidatesGate}`),
 * and printing them as JSON puts braces, quotes and `null`s on a phone screen where somebody is
 * reading a decision. This is the desktop's `decisionText` copied, not reinterpreted: the same entry
 * has to read the same way on both consoles or the two screens cannot be compared.
 */
export function decisionText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(decisionText).filter((line) => line !== '').join(' · ');
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, inner]) => `${key}: ${decisionText(inner)}`)
      .filter((line) => !line.endsWith(': '))
      .join(' · ');
  }
  return String(value);
}

/**
 * One recorded design decision, as the phone shows it: what was chosen and why, in one line.
 *
 * The desktop gets a card with the alternatives and the risks; a sheet that tall has to stay short.
 * A decision whose fields are not the ones this console knows falls back to `decisionText`, which is
 * still more readable than a JSON dump and loses nothing.
 */
export function decisionLine(value: unknown): string {
  const entry = (value ?? {}) as Record<string, unknown>;
  const parts = [entry.subject, entry.rationale].filter((x) => typeof x === 'string' && x.trim() !== '');
  return parts.length ? parts.join(' — ') : decisionText(value);
}

export { t };
