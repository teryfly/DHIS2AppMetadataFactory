import { useMemo, type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createQueryClient } from '@/api/queryClient';
import { ToastHost } from './ToastHost';
import { ErrorBoundary } from './ErrorBoundary';

/**
 * Server state is served exclusively through TanStack Query (UI-APP-006). One client per app
 * tree; the error cache bridges to the toast host (UI-REQ-002).
 */
export function AppProviders({ children }: { children: ReactNode }) {
  const queryClient = useMemo(() => createQueryClient(), []);
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        {children}
        <ToastHost />
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
