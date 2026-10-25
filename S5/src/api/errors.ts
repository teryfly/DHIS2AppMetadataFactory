/*
 * S1 returns a single error envelope on every non-2xx (verified against the S1 REST
 * middleware). UI-REQ-002 maps `errorCode` to a localised message, always surfaces
 * `correlationId`, and offers "retry" when `retryable` is true.
 *
 * Wire shape (S1 `middleware._error_response` / `envelope.py`):
 *   { errorCode, message, stage, artifactId, retryable, correlationId, details }
 */
import { z } from 'zod';

export const ErrorEnvelopeSchema = z.object({
  errorCode: z.string(),
  message: z.string().optional(),
  stage: z.string().nullable().optional(),
  artifactId: z.string().nullable().optional(),
  retryable: z.boolean().optional(),
  correlationId: z.string().optional(),
  details: z.record(z.string(), z.unknown()).optional(),
});

export type ErrorDetails = Record<string, unknown>;

export class ApiError extends Error {
  readonly errorCode: string;
  readonly correlationId?: string;
  readonly retryable: boolean;
  readonly details: ErrorDetails;
  readonly httpStatus: number;
  /** True for network-level failures (fetch threw before a response). COMMIT must not auto-resubmit these (UI-BR-05). */
  readonly networkError: boolean;

  constructor(init: {
    errorCode: string;
    httpStatus: number;
    message?: string;
    correlationId?: string;
    retryable?: boolean;
    details?: ErrorDetails;
    networkError?: boolean;
  }) {
    super(init.message ?? init.errorCode);
    this.name = 'ApiError';
    this.errorCode = init.errorCode;
    this.httpStatus = init.httpStatus;
    this.correlationId = init.correlationId;
    this.retryable = init.retryable ?? defaultRetryable(init.errorCode);
    this.details = init.details ?? {};
    this.networkError = init.networkError ?? false;
  }

  /** Codes the UI special-cases for automatic refresh (UI-REQ-003). */
  get isConflict(): boolean {
    return this.httpStatus === 409 || this.errorCode === 'CONCURRENCY_ERROR' || this.errorCode === 'IDEMPOTENCY_CONFLICT';
  }
  get isApprovalInvalidated(): boolean {
    return this.errorCode === 'APPROVAL_INVALIDATED';
  }
  get isOperatorRequired(): boolean {
    return this.errorCode === 'OPERATOR_REQUIRED';
  }
  get isNotImplemented(): boolean {
    return this.httpStatus === 501;
  }
  /** 401 means the session is gone, not that this call was wrong (UI-AUTH-002). */
  get isUnauthenticated(): boolean {
    return this.httpStatus === 401 || this.errorCode === 'UNAUTHENTICATED';
  }
  /** A cookie-authenticated change without its CSRF value: refuse, do not re-login (UI-AUTH-002). */
  get isCsrfFailed(): boolean {
    return this.errorCode === 'CSRF_FAILED';
  }
}

/** Fallback retryability when S1 omits the flag. Mirrors the S1 read-heavy retry set. */
export function defaultRetryable(code: string): boolean {
  const retryableCodes = new Set([
    'PROVIDER_UNAVAILABLE',
    'LLM_TIMEOUT',
    'DHIS2_TIMEOUT',
    'CONCURRENCY_ERROR',
    'REQUEST_IN_PROGRESS',
    'INTERNAL_ERROR',
  ]);
  return retryableCodes.has(code);
}

const NETWORK_ERROR = 'NETWORK_ERROR';

/** Build an ApiError from a raw fetch Response, reading and parsing the body. */
export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  let parsed: unknown = null;
  const text = await safeText(response);
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { errorCode: `HTTP_${response.status}`, message: text };
    }
  }
  const envelope = ErrorEnvelopeSchema.safeParse(parsed);
  if (envelope.success) {
    return new ApiError({
      errorCode: envelope.data.errorCode,
      httpStatus: response.status,
      message: envelope.data.message,
      correlationId: envelope.data.correlationId ?? response.headers.get('X-Correlation-Id') ?? undefined,
      retryable: envelope.data.retryable,
      details: envelope.data.details,
    });
  }
  // FastAPI 422 shape: { detail: [...] } — surface as a validation error.
  const detail = (parsed as { detail?: unknown } | null)?.detail;
  return new ApiError({
    errorCode: response.status === 422 ? 'VALIDATION_ERROR' : `HTTP_${response.status}`,
    httpStatus: response.status,
    message: typeof detail === 'string' ? detail : undefined,
    correlationId: response.headers.get('X-Correlation-Id') ?? undefined,
    details: detail ? { detail } : {},
  });
}

export function networkApiError(cause: unknown): ApiError {
  return new ApiError({
    errorCode: NETWORK_ERROR,
    httpStatus: 0,
    retryable: true,
    networkError: true,
    message: cause instanceof Error ? cause.message : undefined,
  });
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return '';
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/**
 * CP-UI-022: S1 separates "the object is gone" from "the stage that would produce it has not
 * run" with `details.reason`. Only the second is a state a screen explains in place, so only the
 * second may stop a read being reported — a bare NOT_FOUND still reaches the operator.
 */
export function isNotGenerated(error: unknown): boolean {
  if (!isApiError(error)) return false;
  const reason = (error.details as { reason?: unknown } | undefined)?.reason;
  return reason === 'NOT_GENERATED' && (error.httpStatus === 404 || error.errorCode === 'NOT_FOUND');
}
