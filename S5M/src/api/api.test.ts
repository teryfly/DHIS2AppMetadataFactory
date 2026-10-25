import { describe, it, expect } from 'vitest';
import { toApiError, AppError } from './errors';
import { actionIntent, idempotencyKeyFor, releaseIdempotencyKey, hashJson } from './idempotency';
import { indexActions, resolveAvailability } from './actions';
import { operatorHeader } from './http';

describe('errors (UM-NET-002)', () => {
  it('maps a full S1 envelope', () => {
    const e = toApiError({ errorCode: 'GATE_NOT_APPROVED', message: 'nope', details: { gate: 'g1' } }, 403, { 'x-correlation-id': 'c-1' });
    expect(e).toBeInstanceOf(AppError);
    expect(e.errorCode).toBe('GATE_NOT_APPROVED');
    expect(e.httpStatus).toBe(403);
    expect(e.details).toEqual({ gate: 'g1' });
    expect(e.correlationId).toBe('c-1');
    expect(e.retryable).toBe(false);
  });

  it('flags default-retryable codes and conflict detection', () => {
    const e = toApiError({ errorCode: 'CONCURRENCY_ERROR' }, 409, {});
    expect(e.retryable).toBe(true);
    expect(e.isConflict).toBe(true);
    expect(toApiError({ errorCode: 'APPROVAL_INVALIDATED' }, 409, {}).isApprovalInvalidated).toBe(true);
  });

  it('falls back to VALIDATION_ERROR for FastAPI 422 and HTTP_<status> otherwise', () => {
    expect(toApiError({ detail: [{ msg: 'bad' }] }, 422, {}).errorCode).toBe('VALIDATION_ERROR');
    expect(toApiError({}, 503, {}).errorCode).toBe('HTTP_503');
  });

  it('network errors are retryable and flagged', () => {
    const e = AppError.network('boom');
    expect(e.networkError).toBe(true);
    expect(e.retryable).toBe(true);
    expect(e.httpStatus).toBe(0);
  });
});

describe('idempotency (UM-NET-001)', () => {
  it('reuses the same key for the same intent, releases then re-mints', () => {
    const intent = actionIntent('POST', '/questions/{id}/answer', 'q1', 's1');
    const k1 = idempotencyKeyFor(intent);
    const k2 = idempotencyKeyFor(intent);
    expect(k1).toBe(k2);
    releaseIdempotencyKey(intent);
    const k3 = idempotencyKeyFor(intent);
    expect(k3).not.toBe(k1);
  });

  it('different intents get different keys', () => {
    expect(idempotencyKeyFor(actionIntent('POST', '/x', 'a'))).not.toBe(idempotencyKeyFor(actionIntent('POST', '/x', 'b')));
  });

  it('hashJson is deterministic and body-sensitive (edit-and-resubmit mints a fresh key)', () => {
    expect(hashJson({ patch: { a: 1 } })).toBe(hashJson({ patch: { a: 1 } }));
    expect(hashJson({ patch: { a: 1 } })).not.toBe(hashJson({ patch: { a: 2 } }));
    const base = actionIntent('POST', '/stages/patches', 's1', hashJson({ patch: { v: 1 } }));
    const edited = actionIntent('POST', '/stages/patches', 's1', hashJson({ patch: { v: 2 } }));
    const retry = actionIntent('POST', '/stages/patches', 's1', hashJson({ patch: { v: 1 } }));
    expect(base).toBe(retry); // same body → same intent → reused key
    expect(base).not.toBe(edited); // changed body → different intent → new key (no IDEMPOTENCY_CONFLICT)
  });
});

describe('allowed-actions (UM-BR-01/02)', () => {
  it('missing entry is never assumed available', () => {
    const idx = indexActions({ allowed: [{ action: 'stage.run', available: true }], scope: 'workflow' });
    expect(resolveAvailability(idx, 'gate.approve').available).toBe(false);
    expect(resolveAvailability(idx, 'gate.approve').unknown).toBe(true);
    expect(resolveAvailability(idx, 'stage.run').available).toBe(true);
  });

  it('indexes unavailable with a reason code', () => {
    const idx = indexActions({ unavailable: [{ action: 'execution.commit', available: false, reasonCode: 'ENVIRONMENT_LOCKED', targetId: 'dev' }], scope: 'workflow' });
    const a = resolveAvailability(idx, 'execution.commit', 'dev');
    expect(a.available).toBe(false);
    expect(a.reasonCode).toBe('ENVIRONMENT_LOCKED');
    expect(a.unknown).toBe(false);
  });
});

describe('operatorHeader — the X-Operator transport rule (CP-API-002)', () => {
  it('leaves a name that already fits in a header alone', () => {
    expect(operatorHeader('ada')).toBe('ada');
    // Latin-1 is what a header may carry, so accented names travel as they are
    expect(operatorHeader('Renée')).toBe('Renée');
  });

  it('percent-encodes a name a browser cannot send at all', () => {
    expect(operatorHeader('张三')).toBe('%E5%BC%A0%E4%B8%89');
    expect(decodeURIComponent(operatorHeader('张三'))).toBe('张三');
  });
});
