/*
 * The contract this console was generated from, pinned to a number (S5IF-G11).
 *
 * `src/api/generated` is checked in and compiled, so a vendored contract that quietly falls behind
 * what S1 serves still typechecks — the new field is simply absent at runtime, and the screen that
 * needed it degrades into "the server never sends it". That is exactly how `codePrefix` (契约 1.10.0)
 * shipped past S5M: the checked-in types predated it, `tsc` was green, and nothing anyone reads said
 * which version the bundle had been built from. `install.sh` now diffs the generated files before
 * building; this pins the other half — the declared version — so the number is written down in code
 * that fails, rather than only in a changelog someone may not open.
 *
 * S1 asserts the same number against the document its app serves
 * (`S1/tests/api/test_openapi_contract.py::test_document_version_and_servers`).
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** The contract version `contract/openapi.json` and `src/api/generated` were produced from. */
const CONTRACT_VERSION = '1.19.0';
/** Paths that version declares — a second number, because a stale copy can carry the right version
 * string and the wrong surface if someone hand-edits the vendored file. */
const CONTRACT_PATHS = 147;

describe('the vendored contract (S5IF-G11)', () => {
  const document = JSON.parse(readFileSync(resolve(process.cwd(), 'contract/openapi.json'), 'utf-8')) as {
    info: { version: string };
    paths: Record<string, unknown>;
  };

  it('is the version these generated types were built from', () => {
    expect(document.info.version).toBe(CONTRACT_VERSION);
  });

  it('declares the path set that version was counted at', () => {
    expect(Object.keys(document.paths).length).toBe(CONTRACT_PATHS);
  });
});
