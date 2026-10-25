/*
 * Typed S1 client built from the generated OpenAPI (UI-APP-007). Only S1 is ever contacted
 * (UI-IF-001, C-01). The middleware guarantees the cross-cutting headers (00 doc §cross-cutting,
 * UI-APP-002, UI-OP-003):
 *   - Accept-Language  from the language store
 *   - X-Correlation-Id minted per request (unless already set)
 *   - X-Operator       on every state-changing method
 * Idempotency-Key (UI-REQ-001) is supplied per call via `mutatingHeader()` in the request params.
 */
import createClient from 'openapi-fetch';
import type { paths } from './generated/schema';
import { openapiBaseUrl, loadRuntimeConfig } from '@/config/runtime';
import { getAcceptLanguage } from '@/stores/language';
import { getOperatorName } from '@/stores/operator';
import { CSRF_HEADER, getCsrfToken } from '@/stores/csrf';
import { newUuid, idempotencyKeyFor } from './idempotency';
import { ApiError, ErrorEnvelopeSchema, networkApiError } from './errors';

export const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/**
 * `X-Operator` in a form the browser is allowed to send.
 *
 * Header values are restricted to ISO-8859-1 by the Fetch standard, and a Chinese operator name is
 * the normal case for this product: Chromium rejects the request outright, which reads as a server
 * outage rather than as a name. A name that fits travels unchanged; anything else goes out
 * percent-encoded, and S1 decodes it (`rest/middleware.py:decode_operator`, CP-API-002).
 */
export function operatorHeader(name: string): string {
  const withinLatin1 = [...name].every((character) => (character.codePointAt(0) ?? 0) <= 0xff);
  return withinLatin1 ? name : encodeURIComponent(name);
}

export const client = createClient<paths>({
  baseUrl: openapiBaseUrl(),
  // Late-bound so MSW / test interceptors (which patch globalThis.fetch after module load) are seen.
  fetch: (request: Request) => globalThis.fetch(request),
});

client.use({
  onRequest({ request }) {
    const h = request.headers;
    if (!h.has('Accept-Language')) h.set('Accept-Language', getAcceptLanguage());
    if (loadRuntimeConfig().traceCorrelation && !h.has('X-Correlation-Id')) {
      h.set('X-Correlation-Id', newUuid());
    }
    if (MUTATING_METHODS.has(request.method.toUpperCase())) {
      const operator = getOperatorName();
      // UI-OP-003: attach when present. If absent, S1 answers 400 OPERATOR_REQUIRED (CP-API-002).
      if (operator && !h.has('X-Operator')) h.set('X-Operator', operatorHeader(operator));
      // UI-AUTH-001 / FR-SEC-005: a session that rides a cookie is sent by the browser on its own,
      // so the change must also name the value the page can read. S1 checks it against the digest
      // stored on the session and answers 403 CSRF_FAILED without it.
      const csrf = getCsrfToken();
      if (csrf && !h.has(CSRF_HEADER)) h.set(CSRF_HEADER, csrf);
    }
  },
});

/** `params.header` fragment adding an idempotency key for a create/execute action (UI-REQ-001). */
export function mutatingHeader(intent: string): { 'Idempotency-Key': string } {
  return { 'Idempotency-Key': idempotencyKeyFor(intent) };
}

interface FetchResult {
  response: Response;
  data?: unknown;
  error?: unknown;
}

/** Convert a parsed S1 error body (+status/headers) into an ApiError. */
export function toApiError(parsedBody: unknown, status: number, headers?: Headers): ApiError {
  const envelope = ErrorEnvelopeSchema.safeParse(parsedBody);
  if (envelope.success) {
    return new ApiError({
      errorCode: envelope.data.errorCode,
      httpStatus: status,
      message: envelope.data.message,
      correlationId: envelope.data.correlationId ?? headers?.get('X-Correlation-Id') ?? undefined,
      retryable: envelope.data.retryable,
      details: envelope.data.details,
    });
  }
  const detail = (parsedBody as { detail?: unknown } | null)?.detail;
  return new ApiError({
    errorCode: status === 422 ? 'VALIDATION_ERROR' : `HTTP_${status}`,
    httpStatus: status,
    message: typeof detail === 'string' ? detail : undefined,
    correlationId: headers?.get('X-Correlation-Id') ?? undefined,
    details: detail ? { detail } : {},
  });
}

/**
 * Unwrap an openapi-fetch result: return data on success, throw a rich ApiError otherwise, and
 * surface transport failures as a retryable NETWORK_ERROR (never thrown silently as data).
 * `D` is the expected success shape (a generated OpenAPI model); the openapi-fetch return object is
 * assignable to FetchResult because both its success and error variants carry `response`.
 */
export async function unwrap<D = unknown>(run: () => Promise<FetchResult>): Promise<D> {
  let result: FetchResult;
  try {
    result = await run();
  } catch (cause) {
    throw networkApiError(cause);
  }
  if (!result.response.ok) {
    throw toApiError(result.error, result.response.status, result.response.headers);
  }
  return result.data as D;
}
