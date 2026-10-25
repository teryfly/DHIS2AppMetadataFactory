/*
 * The value sets S1 validates these fields against, in one place because both the environment
 * form and the governance form need them. Each list is `satisfies`-checked against the generated
 * literal union, so a value the contract *drops or renames* breaks `npm run typecheck` here
 * rather than a submit. A value the contract *adds* does not fail — the form simply will not
 * offer it until the list here grows, which is the safer direction for a mistake to go in.
 */
import type * as T from '@/api/types';

/**
 * The spellings S1 accepts (`rest/schemas/environments.py:REFERENCE_SCHEMES`).
 *
 * `db://` is the control plane's own encrypted store, produced by the one-time entry control
 * (S5IF-SEC01); `env://` is a deployment environment variable. Those two are the only schemes with
 * a backend in this build, so they are the only two a hint may name — offering an address that
 * cannot resolve is worse than offering fewer addresses. The remaining three stay in this list
 * because S1 accepts them, and an environment written by another deployment may already name one.
 */
export const REFERENCE_SCHEMES = ['db://', 'env://', 'vault://', 'secret://', 'kms://'] as const;

export const ENVIRONMENT_CLASSES = ['DEV', 'TEST', 'UAT', 'PRODUCTION'] as const satisfies
  readonly NonNullable<T.CreateEnvironmentRequest['environmentClass']>[];

export const AUTH_MODES = ['PAT', 'BASIC'] as const satisfies
  readonly NonNullable<T.CreateEnvironmentRequest['authMode']>[];

export const DATA_POLICIES = ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL', 'UNRESTRICTED'] as const satisfies
  readonly NonNullable<T.CreateEnvironmentRequest['llmDataPolicy']>[];

export const CREDENTIAL_SCOPES = ['discovery', 'import', 'tracker', 'datastore', 'apps', 'maintenance'] as const satisfies
  readonly T.CredentialSpec['scope'][];

export const PROVIDER_CLASSES = ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL', 'UNRESTRICTED'] as const satisfies
  readonly T.RegisterProviderRequest['providerClass'][];

/** A Secret *reference*, never a value — the only thing S1 will receive (S5IF-G09, AC-13). */
export function isSecretReference(value: string): boolean {
  const text = (value || '').trim();
  return REFERENCE_SCHEMES.some((scheme) => text.startsWith(scheme));
}

/**
 * S4 requires both of these from every environment class (`environment/rules.py:85-96`);
 * PRODUCTION additionally requires that the two references differ (DX-AC-02).
 */
export const REQUIRED_CREDENTIAL_SCOPES = ['discovery', 'import'] as const;

/**
 * True only for a parseable `https:` address. Written with `URL` rather than a prefix literal
 * because UM-AC-20 / S5M's isolation test forbids any absolute address appearing in this app —
 * the mobile client must never be able to name a DHIS2 instance to talk to (UM-IF-001).
 */
export function isHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * Any absolute http(s) address. S4 refuses a `baseUrl` that is not one of these at registration,
 * so the form checks it whether or not TLS is required: an address with no scheme at all is not a
 * "plain http" case, it is a typo, and turning TLS off does not make it usable.
 */
export function isHttpOrHttpsUrl(value: string): boolean {
  try {
    const protocol = new URL(value).protocol;
    return protocol === 'https:' || protocol === 'http:';
  } catch {
    return false;
  }
}

/*
 * The blueprint draft's requirement vocabulary, from S2's own section schema
 * (`factory_blueprint/schemas/1/sections.json`), which S1 serves verbatim at
 * `GET /projects/{id}/blueprint/section-schemas/requirements`. These lists are the *fallback* the
 * form uses when that schema has not arrived or spells something differently — `requirementRules()`
 * reads the served document first, so a server-side enum change moves the form without a release.
 *
 * Not to be confused with the NRR's vocabulary (`nrrStatus` = CONFIRMED | NEEDS_REVIEW): that is
 * S3's parse outcome, this is a blueprint entry's own state.
 */
export const REQUIREMENT_PRIORITIES = ['MUST', 'SHOULD', 'COULD', 'WONT'] as const;
export const REQUIREMENT_STATUSES = ['CANDIDATE', 'CONFIRMED', 'REJECTED'] as const;
export const OPEN_QUESTION_SEVERITIES = ['LOW', 'MEDIUM', 'HIGH'] as const;

/** `definitions.businessKey`: a lowercase first segment, then dot/underscore/colon/dash segments. */
export const BUSINESS_KEY_PATTERN = /^[a-z][a-z0-9]*([._:-][A-Za-z0-9_-]+)*$/;
export const BUSINESS_KEY_MIN = 2;
export const BUSINESS_KEY_MAX = 255;
export const STATEMENT_MAX = 4096;
export const SOURCE_NOTE_MAX = 1024;

export function isBusinessKey(value: string): boolean {
  const text = (value || '').trim();
  return text.length >= BUSINESS_KEY_MIN && text.length <= BUSINESS_KEY_MAX && BUSINESS_KEY_PATTERN.test(text);
}
