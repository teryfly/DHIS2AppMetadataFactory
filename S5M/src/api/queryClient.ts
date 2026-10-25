import { QueryClient, QueryCache, MutationCache } from '@tanstack/vue-query';
import { AppError } from '@/api/errors';
import { pushError } from '@/lib/toast';
import { isGuidance } from '@/api/notGenerated';

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof AppError) {
    if (error.networkError) return true;
    return error.retryable && error.httpStatus >= 500;
  }
  return false;
}

let active: QueryClient | null = null;

export function createQueryClient(): QueryClient {
  const client = new QueryClient({
    // a derived read that is not ready yet is explained by the screen itself (CP-UI-022);
    // mutations keep announcing, because a write that failed is never guidance.
    //
    // A 401 on a read is dropped for the same reason in a different shape: with an expired session
    // every open screen's read fails at once, so announcing each one describes one event as a wall
    // of problems. `stores/auth.ts` turns the same signal into the sign-in panel, which is the
    // answer the operator can act on (UM-NET-002).
    queryCache: new QueryCache({
      onError: (error) => {
        if (error instanceof AppError && error.isUnauthenticated) return;
        if (!isGuidance(error)) pushError(error);
      },
    }),
    mutationCache: new MutationCache({ onError: (error) => pushError(error) }),
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: shouldRetry,
      },
      mutations: { retry: 0 },
    },
  });
  active = client;
  return client;
}

/**
 * The client the running app was given, for code that lives outside a component setup and so
 * cannot `useQueryClient()`. The auth store needs it: a sign-in changes which principal every
 * cached read belongs to (UM-P-01), and nothing else re-asks S1 for it.
 */
export function activeQueryClient(): QueryClient | null {
  return active;
}
