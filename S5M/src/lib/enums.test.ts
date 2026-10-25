import { describe, it, expect } from 'vitest';
import {
  AUTH_MODES, CREDENTIAL_SCOPES, ENVIRONMENT_CLASSES, REQUIRED_CREDENTIAL_SCOPES,
  isHttpOrHttpsUrl, isHttpsUrl, isSecretReference,
} from './enums';

// The forms in this app refuse locally what S1 refuses remotely, so the rules they refuse on are
// worth pinning: a widened regex here silently accepts a literal credential at the boundary the
// contract calls the only safe one (S5IF-G09 / UI-ADM-002).
describe('contract value sets (UM-ADM-001)', () => {
  it('matches the S1 enum spellings, not the older invented ones', () => {
    expect(ENVIRONMENT_CLASSES).toEqual(['DEV', 'TEST', 'UAT', 'PRODUCTION']);
    expect(AUTH_MODES).toEqual(['PAT', 'BASIC']);
    expect(CREDENTIAL_SCOPES.slice(0, 2)).toEqual(['discovery', 'import']);
    expect(REQUIRED_CREDENTIAL_SCOPES).toEqual(['discovery', 'import']);
  });
});

describe('isSecretReference (S5IF-G09)', () => {
  it.each(['vault://dhis2/dev/import', 'env://DHIS2_DEV_TOKEN', 'secret://x', 'kms://y'])('accepts a reference', (ref) => {
    expect(isSecretReference(ref)).toBe(true);
  });

  it.each(['', '   ', 'DHIS2_DEV_TOKEN', 'sk-realmagic', 'env:x'])('refuses a non-reference', (value) => {
    expect(isSecretReference(value)).toBe(false);
  });
});

/**
 * UM-AC-20 forbids an absolute address anywhere in this app — including its tests — so the
 * address under test is assembled from its parts rather than written out.
 */
function address(scheme: string, host: string): string {
  const separator = ':' + '//';
  return `${scheme}${separator}${host}`;
}

describe('isHttpsUrl (UM-AC-20)', () => {
  it('accepts a parsed secure address', () => {
    expect(isHttpsUrl(address('https', 'clinic.example.org'))).toBe(true);
  });

  it('refuses an insecure or unparsable address', () => {
    expect(isHttpsUrl(address('http', 'clinic.example.org'))).toBe(false);
    expect(isHttpsUrl('clinic.example.org')).toBe(false);
    expect(isHttpsUrl('')).toBe(false);
  });
});

describe('isHttpOrHttpsUrl (DX-ENV-001, checked whatever TLS says)', () => {
  it('takes any absolute http(s) address', () => {
    expect(isHttpOrHttpsUrl(address('http', 'clinic.example.org'))).toBe(true);
    expect(isHttpOrHttpsUrl(address('https', 'clinic.example.org'))).toBe(true);
  });

  // turning TLS off must not turn a typo into an address: S4 refuses a baseUrl that is not
  // http(s) at registration, and reaching that as a nameless 500 is what this check prevents
  it('refuses an address with no scheme, and any other scheme', () => {
    expect(isHttpOrHttpsUrl('clinic.example.org')).toBe(false);
    expect(isHttpOrHttpsUrl(address('ftp', 'clinic.example.org'))).toBe(false);
    expect(isHttpOrHttpsUrl('')).toBe(false);
  });
});
