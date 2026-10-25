import { describe, expect, it } from 'vitest';
import { AppError } from '@/api/errors';
import { missingArtifactIntent, packageIntentOf, readIntentOf } from '@/api/notGenerated';

function notFound(details: Record<string, unknown> = {}): AppError {
  return new AppError({ errorCode: 'NOT_FOUND', httpStatus: 404, details });
}

describe('UM-REV-007 · readIntentOf', () => {
  it('reads S1 CP-UI-022 as shipped: reason + requiredStage', () => {
    const intent = readIntentOf(notFound({ reason: 'NOT_GENERATED', requiredStage: 'BASELINE_COMPILE', runId: 'r1' }), 'graph');
    expect(intent).toEqual({ kind: 'notGenerated', requiredStage: 'BASELINE_COMPILE', surface: 'graph' });
  });

  it('falls back to details.stage when requiredStage is absent', () => {
    const intent = readIntentOf(notFound({ reason: 'NOT_GENERATED', stage: 'HANDOFF' }), 'package');
    expect(intent.kind === 'notGenerated' && intent.requiredStage).toBe('HANDOFF');
  });

  it('reads NOT_EXIST as "content does not exist"', () => {
    expect(readIntentOf(notFound({ reason: 'NOT_EXIST' }), 'graph')).toEqual({ kind: 'notExist', surface: 'graph' });
  });

  // Transitional: S1 has not implemented CP-UI-022 yet (grep for NOT_GENERATED in S1 = 0 hits),
  // and raises a plain NOT_FOUND with details={projectId} for an uncompiled blueprint.
  it('treats a reason-less NOT_FOUND on a derived resource as not generated', () => {
    // UM-AC-19 names the stage, so the shim has to supply one when S1 supplies nothing.
    const stages: Record<string, string> = { graph: 'BASELINE_COMPILE', registry: 'BASELINE_COMPILE', apiDoc: 'BASELINE_COMPILE', uiSpec: 'BASELINE_COMPILE', package: 'HANDOFF', issues: 'BASELINE_COMPILE' };
    for (const surface of Object.keys(stages) as (keyof typeof stages)[]) {
      const intent = readIntentOf(notFound({ projectId: 'p1' }), surface as never);
      expect(intent.kind, surface).toBe('notGenerated');
      expect(intent.kind === 'notGenerated' && intent.requiredStage, surface).toBe(stages[surface]);
    }
  });

  it('keeps an unknown reason as notExist rather than guessing a stage', () => {
    const intent = readIntentOf(notFound({ reason: 'PURGED' }), 'graph');
    expect(intent).toEqual({ kind: 'notExist', surface: 'graph' });
  });

  it('passes real failures through untouched', () => {
    const boom = new AppError({ errorCode: 'INTERNAL_ERROR', httpStatus: 500, message: 'boom' });
    const intent = readIntentOf(boom, 'graph');
    expect(intent.kind).toBe('error');
  });

  it('treats no error as ok', () => {
    expect(readIntentOf(null, 'graph')).toEqual({ kind: 'ok' });
  });
});

describe('UM-REV-007 · package kind=NONE', () => {
  it('maps the pre-CP-UI-022 success-with-NONE body to guidance', () => {
    expect(packageIntentOf({ kind: 'NONE', files: [] })).toEqual({ kind: 'notGenerated', requiredStage: 'HANDOFF', surface: 'package' });
  });

  it('is silent once a package exists', () => {
    expect(packageIntentOf({ kind: 'BUSINESS_INPUT', files: [{ path: 'a' }] }).kind).toBe('ok');
  });

  it('is silent for a null payload (still loading)', () => {
    expect(packageIntentOf(null).kind).toBe('ok');
  });
});

describe('UM-REV-007 · artifact-backed resources', () => {
  it('a workflow with no OPENAPI artifact is guided, not an error', () => {
    expect(missingArtifactIntent('apiDoc')).toEqual({ kind: 'notGenerated', requiredStage: 'BASELINE_COMPILE', surface: 'apiDoc' });
  });
});
