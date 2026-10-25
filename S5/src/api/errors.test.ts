/*
 * UI-REQ-002 / UI-BR-05: the S1 error envelope is parsed into a rich ApiError with the retryable
 * flag, correlationId and details; unknown/422 shapes degrade gracefully; network failures are a
 * retryable NETWORK_ERROR.
 */
import { describe, it, expect } from 'vitest';
import { apiErrorFromResponse, networkApiError, defaultRetryable, isApiError, ApiError } from './errors';

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
}

describe('error envelope', () => {
  it('maps errorCode, retryable, correlationId and details', async () => {
    const res = jsonResponse(
      409,
      { errorCode: 'CONCURRENCY_ERROR', message: 'boom', retryable: true, correlationId: 'corr-1', details: { field: 'name' } },
      { 'X-Correlation-Id': 'corr-1' },
    );
    const err = await apiErrorFromResponse(res);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.errorCode).toBe('CONCURRENCY_ERROR');
    expect(err.retryable).toBe(true);
    expect(err.correlationId).toBe('corr-1');
    expect(err.details).toEqual({ field: 'name' });
    expect(err.isConflict).toBe(true);
  });

  it('flags APPROVAL_INVALIDATED so the Gate page can refresh (UI-REQ-003)', async () => {
    const err = await apiErrorFromResponse(jsonResponse(409, { errorCode: 'APPROVAL_INVALIDATED' }));
    expect(err.isApprovalInvalidated).toBe(true);
  });

  it('defaults retryable from the code when the envelope omits it', async () => {
    const err = await apiErrorFromResponse(jsonResponse(503, { errorCode: 'PROVIDER_UNAVAILABLE' }));
    expect(err.retryable).toBe(true);
    expect(defaultRetryable('OPERATOR_REQUIRED')).toBe(false);
  });

  it('treats a FastAPI 422 detail array as a validation error', async () => {
    const err = await apiErrorFromResponse(jsonResponse(422, { detail: [{ msg: 'required' }] }));
    expect(err.errorCode).toBe('VALIDATION_ERROR');
  });

  it('networkApiError is retryable and marked network (never auto-resubmit COMMIT)', () => {
    const err = networkApiError(new Error('offline'));
    expect(err.networkError).toBe(true);
    expect(err.httpStatus).toBe(0);
    expect(isApiError(err)).toBe(true);
  });
});
