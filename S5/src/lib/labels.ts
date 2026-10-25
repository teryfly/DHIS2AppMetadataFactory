/*
 * Display words for values the server sends as internal tokens. The server's vocabulary is not the
 * operator's: `/setup` names its capability backends `S2`/`S3`/`S4` and reports each as `real` or
 * `fake`, and an artifact row carries the subsystem code that created it. None of that is a word a
 * project owner or approver can act on, so every render site goes through these functions.
 *
 * Two rules, both earned:
 *   - An unmapped token returns **the raw token**, never an empty string. The `providers` map is
 *     `{[key: string]: string}` in the contract, so a deployment can arrive with a key this file has
 *     never heard of; a blank row reads as "no capability" instead of "unrecognised capability".
 *   - The i18n keys are written as literals inside a `switch`, not assembled from a template, so
 *     `src/i18n/keysUsed.test.ts` actually verifies each one exists in both languages.
 */
import i18n from '@/i18n';

const t = (key: string, options?: Record<string, string>): string => String(i18n.t(key, options));

/** The capability backend behind a `/setup` provider key, or an artifact's creating subsystem. */
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

/** Whether a capability is wired to the real implementation or to the in-process stand-in. */
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

/** One `providers` entry as the operator reads it: "Requirement parsing: demo stand-in". */
export function capabilityEntry(code: string, mode: string): string {
  return t('capabilities.item', { name: capabilityLabel(code), mode: modeLabel(mode) });
}

/**
 * Which gate this is, in the words the operator already knows for the work it closes.
 *
 * A gate is named after the stage it stops (`S1/.../domain/enums.py:_GATE_STAGE`), so the seven gate
 * types reuse the seven stage words instead of inventing a second vocabulary — and the pairings are
 * written out rather than derived, because `ARCHITECTURE_GATE` closes MAPPING and `PACKAGE_GATE`
 * closes STATIC_VALIDATION: reading the token itself would get both wrong.
 */
export function gateTypeLabel(type?: string | null): string {
  if (!type) return '';
  switch (type) {
    case 'REQUIREMENT_GATE':
    case 'REQUIREMENTS_GATE':
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

/**
 * Which of the two delivery lines a project is on.
 *
 * The wire carries `A` / `B`, which is S1's shorthand rather than a word an operator can read: the
 * difference is who designs the pages, and it decides which stages exist at all. The two sentences
 * are the ones the new-project wizard already offers for the same choice (`wizard.modeA` /
 * `wizard.modeB`) — a third wording here would be a third name for one thing.
 */
export function deliveryModeLabel(mode?: string | null): string {
  if (!mode) return '';
  switch (mode) {
    case 'A':
      return t('wizard.modeA');
    case 'B':
      return t('wizard.modeB');
    default:
      return mode;
  }
}

/**
 * Words for the token→word blocks in `src/i18n/messages.ts` (`roles`, `milestone`, `disposition`, …).
 *
 * These are dictionaries keyed by the server's own token, so the key is assembled rather than
 * written out: `src/lib/vocabulary.test.ts` asserts every token the server can send has a word in
 * both languages, which a literal-key scan cannot do for a computed one. Unknown tokens stay raw —
 * see the file header.
 */
const word = (block: string, token?: string | null): string =>
  token ? String(i18n.t(`${block}.${token}`, { defaultValue: token })) : '';

/** The role a person holds. The wire carries `SOLUTION_ARCHITECT`; the screen should not. */
export const roleLabel = (role?: string | null): string => word('roles', role);

/** How far a project has got. Distinct from `stageStatus`: a project's own milestone, not a run. */
export const milestoneLabel = (status?: string | null): string => word('milestone', status);

/** What a registry row proposes to do to an object. */
export const dispositionLabel = (value?: string | null): string => word('disposition', value);

/** What kind of thing a gate or warning is bound to. */
export const refTypeLabel = (value?: string | null): string => word('refType', value);

/** How bad something is: an issue level or a question severity. */
export const severityLabel = (value?: string | null): string => word('severity', value);

export const nrrPriorityLabel = (value?: string | null): string => word('nrrPriority', value);

export const nrrStatusLabel = (value?: string | null): string => word('nrrStatus', value);

/** A stage or agent-run status as the run log names it. */
export const runStatusLabel = (value?: string | null): string => word('runStatus', value);

/** One of the thirteen normalised requirement categories a parse may produce. */
export function nrrCategoryLabel(value?: string | null): string {
  if (!value) return '';
  switch (value) {
    case 'businessGoal':
      return t('nrrCategory.businessGoal');
    case 'participant':
      return t('nrrCategory.participant');
    case 'businessEntity':
      return t('nrrCategory.businessEntity');
    case 'useCase':
      return t('nrrCategory.useCase');
    case 'businessProcess':
      return t('nrrCategory.businessProcess');
    case 'businessRule':
      return t('nrrCategory.businessRule');
    case 'dataRequirement':
      return t('nrrCategory.dataRequirement');
    case 'organisationRequirement':
      return t('nrrCategory.organisationRequirement');
    case 'accessRequirement':
      return t('nrrCategory.accessRequirement');
    case 'reportRequirement':
      return t('nrrCategory.reportRequirement');
    case 'notificationRequirement':
      return t('nrrCategory.notificationRequirement');
    case 'i18nRequirement':
      return t('nrrCategory.i18nRequirement');
    case 'openQuestion':
      return t('nrrCategory.openQuestion');
    default:
      return value;
  }
}

/** One state of the cost budget. `WARNING` here is the budget, not an issue level. */
export const budgetStateLabel = (value?: string | null): string => word('budgetState', value);

export const handoffKindLabel = (value?: string | null): string => word('handoffKind', value);

export const waiverLabel = (value?: string | null): string => word('waiver', value);

/** Which artifact this is. The same set names a row of the artifact list and a graph node. */
export const artifactKindLabel = (value?: string | null): string => word('artifactKinds', value);

/** How sensitive the content is. */
export const classificationLabel = (value?: string | null): string => word('inputs.classes', value);

/** Any status token: the one block `StatusBadge` reads, kept available for plain text as well. */
export const statusLabel = (value?: string | null): string => word('stageStatus', value);

/** What a diff of two Blueprint versions did to one object. */
export const diffStateLabel = (value?: string | null): string => word('diffState', value);

/** Which objects an environment lets the Factory delete. */
export const deletionPolicyLabel = (value?: string | null): string => word('deletionPolicy', value);

/** How the Factory authenticates to an environment: a token, or a name and password. */
export const authModeLabel = (value?: string | null): string =>
  value ? String(i18n.t(`environments.auth${value}`, { defaultValue: value })) : '';

/** Which part of an environment a credential lets the Factory touch. */
export const credentialScopeLabel = (value?: string | null): string =>
  value ? String(i18n.t(`environments.scope${value.toUpperCase()}`, { defaultValue: value })) : '';

/** How far data may travel out of this deployment on its way to a model. */
export const dataPolicyLabel = (value?: string | null): string =>
  value ? String(i18n.t(`environments.policy${value}`, { defaultValue: value })) : '';

/**
 * A grouping axis of the usage rollup. Which axes exist is S3's answer, so an unknown one keeps its
 * own name rather than being dropped: the operator can still tick it, it just is not renamed here.
 */
export const usageDimensionLabel = (value?: string | null): string =>
  value ? String(i18n.t(`usageDimensions.${value}`, { defaultValue: value })) : '';

/**
 * One cell of the usage rollup.
 *
 * The `stage` and `agent` axes hold tokens S1 and S3 chose, so they read in the operator's language;
 * `model` holds the name somebody typed when they registered the model, which is data — renaming it
 * would make the row impossible to find again in the model list.
 */
export function usageValueLabel(dimension: string, value: string): string {
  if (dimension === 'stage') return stageTypeLabel(value);
  if (dimension === 'agent') return word('agents', value);
  return value;
}

/** Which way a platform notification leaves the factory. */
export const channelLabel = (value?: string | null): string => word('notificationChannel', value?.toUpperCase());

/** Which of the three classes an environment belongs to. */
export const envClassLabel = (value?: string | null): string =>
  value ? String(i18n.t(`environments.class${value}`, { defaultValue: value })) : '';

/** Which stage this is — the same thirteen words the stage rail uses, for text outside it. */
export const stageTypeLabel = (value?: string | null): string => word('stageTypes', value);

/** Whether a capability profile still describes the instance it was read from. */
export const freshnessLabel = (value?: string | null): string => word('freshness', value);

/**
 * Which kind of notice this is. The key is the server's own `NotificationType` token — this block
 * used to be written lowercase, which no lookup could hit, so every row showed the raw token.
 */
export const notificationTypeLabel = (value?: string | null): string =>
  value ? word('notifications.types', value) : word('notifications.types', 'default');

/** How an account signs in. */
export const identitySourceLabel = (value?: string | null): string => word('identitySource', value);

/** Which kind of project this is: new pages, or pages fitted onto existing metadata. */
export function projectTypeLabel(value?: string | null): string {
  if (!value) return '';
  switch (value) {
    case 'GREENFIELD':
      return t('wizard.typeGreenfield');
    case 'BROWNFIELD':
      return t('wizard.typeBrownfield');
    default:
      return value;
  }
}


/**
 * A project's workflow state.
 *
 * `ACTIVE` is one token for two different sentences: an environment is ACTIVE when nobody has
 * switched it off, a project's workflow is ACTIVE while it still has work going on. The shared word
 * is the environment's, so the project asks for the running one.
 */
export function workflowStatusLabel(value?: string | null): string {
  if (!value) return '';
  return value === 'ACTIVE' ? t('stageStatus.IN_PROGRESS') : word('stageStatus', value);
}
