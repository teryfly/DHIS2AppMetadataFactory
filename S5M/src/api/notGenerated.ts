import type { AppError } from '@/api/errors';

/**
 * UM-REV-007 (S1 CP-UI-022): derived resources that do not exist *yet* — the dependency graph,
 * the development package, the OpenAPI and UI Specification documents — must read as
 * "finish stage X, then this is viewable", never as a blank page or a generic 404.
 *
 * This module is the only place that decides that. Pages consume `ReadIntent`; when S1 ships the
 * `details.reason` envelope, deleting the shim branches below changes nothing above this line.
 */

export type GeneratedSurface = 'graph' | 'registry' | 'package' | 'apiDoc' | 'uiSpec' | 'issues';

export type ReadIntent =
  | { kind: 'ok' }
  | { kind: 'notGenerated'; requiredStage?: string; surface: GeneratedSurface }
  | { kind: 'notExist'; surface: GeneratedSurface }
  | { kind: 'error'; error: AppError; surface: GeneratedSurface };

// UM-REV-007 names the graph, the development package, the OpenAPI and the UI Specification, and
// says 等 — S1 raises the same no-version NOT_FOUND for the registry and the validation report
// (blueprint_reads.py), so they belong in the same set. A resource outside this set that 404s is a
// genuinely missing object and must not be reported as a missing build.
/**
 * Which stage produces each derived resource, verified against the producers rather than guessed:
 * a blueprint version is what the graph, the registry and the validation reports need
 * (`blueprint_reads.py` raises when no version resolves), and S2's `compile_baseline` is what
 * emits the OPENAPI and UI_SPECIFICATION artifacts. The delivery package only exists after
 * HANDOFF. UM-AC-19 names the stage in the sentence, so this table is what fills the gap when
 * S1 answers a bare NOT_FOUND without `details.requiredStage`.
 */
export const PRODUCING_STAGE: Record<GeneratedSurface, string> = {
  graph: 'BASELINE_COMPILE',
  registry: 'BASELINE_COMPILE',
  issues: 'BASELINE_COMPILE',
  apiDoc: 'BASELINE_COMPILE',
  uiSpec: 'BASELINE_COMPILE',
  package: 'HANDOFF',
};


const DERIVED: ReadonlySet<GeneratedSurface> = new Set<GeneratedSurface>(['graph', 'registry', 'package', 'apiDoc', 'uiSpec', 'issues']);

function reasonOf(err: AppError): string {
  const r = err.details?.['reason'];
  return typeof r === 'string' ? r : '';
}

function stageOf(err: AppError): string | undefined {
  for (const key of ['requiredStage', 'stage'] as const) {
    const v = err.details?.[key];
    if (typeof v === 'string' && v) return v;
  }
  return undefined;
}

/** The intended reader-facing outcome of a failed derived-resource read. */
/**
 * Whether this failure is CP-UI-022 guidance rather than something that went wrong: a derived
 * resource that does not exist yet is a state the screen explains in place, and announcing it as
 * an error toast on top of that explanation says the same thing twice, badly.
 */
export function isGuidance(err: unknown): boolean {
  if (!(err instanceof Error) || (err as AppError).name !== 'AppError') return false;
  const e = err as AppError;
  if (e.errorCode !== 'NOT_FOUND' && e.httpStatus !== 404) return false;
  return ['NOT_GENERATED', 'NOT_EXIST'].includes(reasonOf(e));
}

/**
 * The narrower case a read absorbs instead of failing: the resource is missing because the stage
 * that produces it has not run. `NOT_EXIST` is deliberately excluded — a dead reference is a real
 * error and the operator has to hear about it.
 */
export function isNotGenerated(err: unknown): boolean {
  if (!(err instanceof Error) || (err as AppError).name !== 'AppError') return false;
  const e = err as AppError;
  if (e.errorCode !== 'NOT_FOUND' && e.httpStatus !== 404) return false;
  return reasonOf(e) === 'NOT_GENERATED';
}

export function readIntentOf(err: unknown, surface: GeneratedSurface): ReadIntent {
  if (!err) return { kind: 'ok' };
  if (!(err instanceof Error) || (err as AppError).name !== 'AppError') {
    return { kind: 'error', error: err as AppError, surface };
  }
  const e = err as AppError;
  const reason = reasonOf(e);

  if (e.errorCode === 'NOT_FOUND' || e.httpStatus === 404) {
    if (reason === 'NOT_GENERATED') return { kind: 'notGenerated', requiredStage: stageOf(e), surface };
    if (reason === 'NOT_EXIST') return { kind: 'notExist', surface };
    // S1 V1.1.2 上线后删除：S1 has not yet started distinguishing "not generated" from
    // "does not exist". Before CP-UI-022 lands it answers a plain NOT_FOUND with no
    // details.reason for a resource whose build has not run (verified in
    // factory_control/services/blueprint_reads.py, which raises NotFoundControlError with
    // details={projectId} only). A derived resource that is missing is overwhelmingly the
    // not-generated case, so map it there and let the guided empty state name the stage.
    if (!reason && DERIVED.has(surface)) {
      // UM-AC-19 asks for "完成基线编译后可查看": with no reason and no requiredStage from S1 the
      // only way to name the stage is to know which stage produces this resource.
      return { kind: 'notGenerated', requiredStage: stageOf(e) ?? PRODUCING_STAGE[surface], surface };
    }
    return { kind: 'notExist', surface };
  }
  return { kind: 'error', error: e, surface };
}

/**
 * A package listing that answers 200 with `kind: NONE` is S1's pre-CP-UI-022 way of saying
 * "nothing has been delivered yet". Success carries the signal, so it needs its own mapper.
 */
export function packageIntentOf(view: { kind?: string; files?: unknown[] } | null | undefined): ReadIntent {
  const kind = String(view?.kind ?? '');
  const empty = !view?.files?.length;
  if (kind === 'NONE' && empty) {
    // S1 V1.1.2 上线后删除（改由 details.reason 判定）
    return { kind: 'notGenerated', requiredStage: 'HANDOFF', surface: 'package' };
  }
  return { kind: 'ok' };
}

/**
 * An OpenAPI / UI Specification document is an artifact, not an endpoint: when the workflow has no
 * artifact of that type there is no error at all, yet the reader still needs the guided answer.
 */
export function missingArtifactIntent(surface: GeneratedSurface, requiredStage = 'BASELINE_COMPILE'): ReadIntent {
  return { kind: 'notGenerated', requiredStage, surface };
}

/** Collapse an intent back to "is there anything to render?" for list-style pages. */
export function isEmptyIntent(intent: ReadIntent): boolean {
  return intent.kind !== 'ok';
}
