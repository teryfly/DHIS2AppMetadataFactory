/*
 * The value sets S1 validates these fields against. They live in one file because the
 * environment form and the governance form both need them, and because every list is
 * `satisfies`-checked against the generated literal union: when docs/openapi.json drops or
 * renames a value, `npm run typecheck` fails here instead of a submit failing at runtime.
 * A value *added* by the contract does not fail — `satisfies` checks that this list is a
 * subset — so the new choice simply is not offered until the list grows to match.
 */
import type * as T from './types';

/**
 * The spellings S1 accepts (`rest/schemas/environments.py:REFERENCE_SCHEMES`).
 *
 * Only `db://` and `env://` have a backend in this build: `db://` is the control plane's own
 * encrypted store, produced by the "enter a credential" control (S5IF-SEC01), and `env://` is a
 * variable in the deployment's environment file. The other three are accepted for compatibility
 * with a deployment that installed a real secret manager, which this one has not — so the form
 * offers the first two and leaves the rest out of the hints. Showing someone an address that
 * cannot resolve is worse than showing them fewer addresses.
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
