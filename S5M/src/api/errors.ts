/** S1 failure envelope shape (总体 SRS 8.3 / 00 doc 6.11). */
export interface ErrorEnvelope {
  errorCode?: string;
  message?: string;
  stage?: string;
  artifactId?: string;
  retryable?: boolean;
  correlationId?: string;
  details?: Record<string, unknown>;
  /** FastAPI request-validation body (non-envelope 422). */
  detail?: unknown;
}

/** Codes S1 does not always mark retryable but that are safe to retry. */
const DEFAULT_RETRYABLE = new Set([
  'PROVIDER_UNAVAILABLE',
  'LLM_TIMEOUT',
  'DHIS2_TIMEOUT',
  'CONCURRENCY_ERROR',
  'REQUEST_IN_PROGRESS',
  'INTERNAL_ERROR',
]);

export class AppError extends Error {
  errorCode: string;
  httpStatus: number;
  details: Record<string, unknown>;
  correlationId?: string;
  retryable: boolean;
  networkError: boolean;

  constructor(o: {
    errorCode: string;
    httpStatus: number;
    message?: string;
    details?: Record<string, unknown>;
    correlationId?: string;
    retryable?: boolean;
    networkError?: boolean;
  }) {
    super(o.message ?? o.errorCode);
    this.name = 'AppError';
    this.errorCode = o.errorCode;
    this.httpStatus = o.httpStatus;
    this.details = o.details ?? {};
    this.correlationId = o.correlationId;
    this.retryable = o.retryable ?? DEFAULT_RETRYABLE.has(o.errorCode);
    this.networkError = o.networkError ?? false;
  }

  static network(message: string): AppError {
    return new AppError({
      errorCode: 'NETWORK_ERROR',
      httpStatus: 0,
      message,
      retryable: true,
      networkError: true,
    });
  }

  get isConflict(): boolean {
    return (
      this.httpStatus === 409 ||
      this.errorCode === 'CONCURRENCY_ERROR' ||
      this.errorCode === 'IDEMPOTENCY_CONFLICT'
    );
  }

  get isApprovalInvalidated(): boolean {
    return this.errorCode === 'APPROVAL_INVALIDATED';
  }

  get isOperatorRequired(): boolean {
    return this.errorCode === 'OPERATOR_REQUIRED';
  }

  /** A cookie session lost its CSRF pairing: re-authenticate, never retry the same call. */
  get isCsrfFailed(): boolean {
    return this.errorCode === 'CSRF_FAILED';
  }

  /**
   * The session ended while the page was open. The sign-in panel is the answer to this, not a
   * toast: UM-NET-002's screen already says who is here, and a screen full of refused reads
   * describes one expired session as if it were twenty problems.
   */
  get isUnauthenticated(): boolean {
    return this.httpStatus === 401;
  }

  get isNotImplemented(): boolean {
    return this.httpStatus === 501 || this.errorCode === 'AI_FEATURE_NOT_ENABLED' || this.errorCode === 'BP_FEATURE_NOT_ENABLED';
  }
}

export function toApiError(
  body: unknown,
  status: number,
  headers: Record<string, string>,
): AppError {
  const correlationId = headers['x-correlation-id'] ?? headers['X-Correlation-Id'];
  if (body && typeof body === 'object') {
    const env = body as ErrorEnvelope;
    if (env.errorCode) {
      return new AppError({
        errorCode: env.errorCode,
        httpStatus: status,
        message: env.message,
        details: env.details ?? {},
        correlationId: env.correlationId ?? correlationId,
        retryable: env.retryable,
      });
    }
    // FastAPI 422 request-validation shape.
    if (env.detail !== undefined) {
      return new AppError({
        errorCode: 'VALIDATION_ERROR',
        httpStatus: status,
        details: { detail: env.detail },
        correlationId,
      });
    }
  }
  return new AppError({
    errorCode: `HTTP_${status}`,
    httpStatus: status,
    correlationId,
  });
}
