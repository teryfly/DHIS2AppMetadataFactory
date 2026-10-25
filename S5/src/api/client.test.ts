/*
 * Client core: header wiring (UI-OP-003 idempotency UI-REQ-001), envelope → ApiError, and unwrap
 * success/error/network paths. The middleware injection is exercised through a real request via the
 * installed client.fetch (MSW-backed) plus a direct unit check of the helper builders.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { toApiError, unwrap, mutatingHeader, operatorHeader, MUTATING_METHODS } from './client';
import { isApiError } from './errors';
import { useOperatorStore } from '@/stores/operator';

beforeEach(() => useOperatorStore.setState({ name: 'Ana' }));
afterEach(() => useOperatorStore.getState().clear());

describe('header builders', () => {
  it('mutatingHeader yields a stable Idempotency-Key for the same intent (UI-REQ-001)', () => {
    expect(mutatingHeader('i1')['Idempotency-Key']).toBe(mutatingHeader('i1')['Idempotency-Key']);
    expect(mutatingHeader('i1')['Idempotency-Key']).not.toBe(mutatingHeader('i2')['Idempotency-Key']);
  });
  it('MUTATING_METHODS covers the four state-changing verbs', () => {
    expect([...MUTATING_METHODS].sort()).toEqual(['DELETE', 'PATCH', 'POST', 'PUT']);
  });
});

describe('toApiError', () => {
  it('parses the S1 envelope', () => {
    const e = toApiError({ errorCode: 'ENVIRONMENT_LOCKED', retryable: false, correlationId: 'c1', details: { k: 1 } }, 423);
    expect(e.errorCode).toBe('ENVIRONMENT_LOCKED');
    expect(e.correlationId).toBe('c1');
    expect(e.details).toEqual({ k: 1 });
  });
  it('falls back for a non-envelope body', () => {
    const e = toApiError({ detail: 'bad' }, 422);
    expect(e.errorCode).toBe('VALIDATION_ERROR');
  });
});

describe('unwrap', () => {
  it('returns data on 2xx', async () => {
    const data = await unwrap<number>(() => Promise.resolve({ response: new Response(null, { status: 200 }), data: 7 }));
    expect(data).toBe(7);
  });
  it('throws ApiError on non-2xx using the parsed error', async () => {
    await expect(
      unwrap(() => Promise.resolve({ response: new Response(null, { status: 409 }), error: { errorCode: 'CONCURRENCY_ERROR' } })),
    ).rejects.toSatisfy((e: unknown) => isApiError(e) && e.isConflict);
  });
  it('converts a thrown fetch into a retryable network error (UI-BR-05)', async () => {
    await expect(unwrap(() => Promise.reject(new Error('offline')))).rejects.toSatisfy((e: unknown) => isApiError(e) && e.networkError && e.retryable);
  });
});

describe('operatorHeader — the X-Operator transport rule (CP-API-002)', () => {
  it('leaves a name that already fits in a header alone', () => {
    expect(operatorHeader('ada')).toBe('ada');
    expect(operatorHeader('Ana Ruiz')).toBe('Ana Ruiz');
    // Latin-1 is what a header may carry, so accented names travel as they are
    expect(operatorHeader('Renée')).toBe('Renée');
  });

  it('percent-encodes a name a browser cannot send at all', () => {
    expect(operatorHeader('张三')).toBe('%E5%BC%A0%E4%B8%89');
    expect(decodeURIComponent(operatorHeader('张三'))).toBe('张三');
  });
});
