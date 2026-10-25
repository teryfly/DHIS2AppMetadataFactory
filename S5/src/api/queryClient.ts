/*
 * Server state lives ONLY here, sourced from S1 (UI-APP-006, UI-BR-02). Policies:
 *  - Reads retry only on retryable/network failures, and only twice (transient blips).
 *  - Mutations never auto-retry. A COMMIT that fails on the network is surfaced for a *manual*
 *    retry under the same idempotency key (UI-REQ-004, UI-BR-05) — automatic resubmission would
 *    be a second, unintended execution.
 */
import { QueryClient, QueryCache, MutationCache } from '@tanstack/react-query';
import { isApiError } from './errors';
import { qk } from './queryKeys';
import { useCsrfStore } from '@/stores/csrf';
import type { ApiError } from './errors';

export type ApiErrorListener = (error: ApiError, context: { mutation?: boolean }) => void;
const listeners = new Set<ApiErrorListener>();

/** Subscribe to every surfaced API error (used by the global toast host). */
export function onApiError(fn: ApiErrorListener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(error: unknown, context: { mutation?: boolean }): void {
  if (isApiError(error)) {
    for (const fn of listeners) fn(error, context);
  }
}

export function createQueryClient(): QueryClient {
  const client = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => emit(error, { mutation: false }),
    }),
    mutationCache: new MutationCache({
      onError: (error) => emit(error, { mutation: true }),
    }),
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // Never auto-retry an operator/permission/state error; only transient ones, twice.
          if (isApiError(error)) {
            if (error.httpStatus === 0 && error.retryable) return failureCount < 2; // network
            return error.retryable && error.httpStatus >= 500 ? failureCount < 2 : false;
          }
          return false;
        },
      },
      mutations: {
        retry: false, // UI-BR-05: no automatic mutation retry.
      },
    },
  });

  // UI-AUTH-002: a 401 is how the app learns the session lapsed while a page was open. The gate
  // holds the session answer for a minute, so the read that just failed has to be the thing that
  // re-asks it — otherwise, with its toast suppressed, the page simply refuses to write and says
  // nothing. Done at this layer because the client exists here; a hook in the toast host would
  // require a provider that host does not have, and the redirect belongs to the cache anyway.
  client.getQueryCache().subscribe((event) => {
    const error = event.query.state.error;
    if (event.type !== 'updated' || !isApiError(error) || !error.isUnauthenticated) return;
    if (event.query.queryKey[0] === qk.session[0]) return;
    useCsrfStore.getState().markLapsed();
    void client.invalidateQueries({ queryKey: qk.session });
  });

  return client;
}
