/*
 * The contract this console was generated from, pinned to a number (S5IF-G11).
 *
 * `src/api/generated` is checked in and compiled, so a vendored contract that quietly falls behind
 * what S1 serves still typechecks — the new field simply does not exist here, and the first symptom
 * is either code that already sends the field failing to compile, or a form that never learned to
 * offer it. This console is the case study: 契约 1.10.0 added `codePrefix` to
 * `POST /projects`, the vendored copy here was not refreshed, and the wizard's own
 * `NewProjectView.vue` only compiled once the types were regenerated (S5's `tsc` never saw it, so
 * the miss would have shipped). `install.sh` now
 * diffs the generated files before every build; this pins the other half — the declared version —
 * so the number lives in code that fails rather than only in a changelog nobody opens.
 *
 * S1 asserts the same number against the document its app serves
 * (`S1/tests/api/test_openapi_contract.py::test_document_version_and_servers`), and S5 asserts it
 * beside this file.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** The contract version `contract/openapi.json` and `src/api/generated` were produced from. */
const CONTRACT_VERSION = '1.19.0';
/** Paths that version declares — a second number, because a hand-edited copy can carry the right
 * version string and the wrong surface. */
const CONTRACT_PATHS = 147;

describe('the vendored contract (S5IF-G11)', () => {
  const document = JSON.parse(
    readFileSync(resolve(process.cwd(), 'contract/openapi.json'), 'utf-8'),
  ) as { info: { version: string }; paths: Record<string, unknown> };

  it('is the version these generated types were built from', () => {
    expect(document.info.version).toBe(CONTRACT_VERSION);
  });

  it('declares the path set that version was counted at', () => {
    expect(Object.keys(document.paths).length).toBe(CONTRACT_PATHS);
  });
});
