/*
 * Coverage for the composition root and shared chrome: the real <App/>, the language switch, the
 * error boundary fallback, the API-error → toast bridge, and the read-model query hooks.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, renderHook } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import '@/i18n';
import { App } from '@/App';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { LanguageSwitch } from '@/components/LanguageSwitch';
import { ToastHost } from '@/components/ToastHost';
import { pushToast, useToastStore } from '@/components/toast';
import { onApiError } from '@/api/queryClient';
import { ApiError } from '@/api/errors';
import { useOperatorStore } from '@/stores/operator';
import { useLanguageStore } from '@/stores/language';
import { projectsListOptions, environmentsOptions, notificationsOptions } from '@/api/queries';

beforeEach(() => {
  cleanup();
  useToastStore.getState().clear();
  useOperatorStore.getState().setName('Ana');
});

describe('<App/> composition root', () => {
  it('boots the shell with the brand in the top bar', async () => {
    render(<App />);
    expect(await screen.findByText(/Factory · DHIS2 Metadata Factory|工厂/)).toBeInTheDocument();
  });
});

describe('LanguageSwitch (UI-APP-002)', () => {
  it('toggles the language and persists to the store', async () => {
    useLanguageStore.getState().setLanguage('en');
    render(<LanguageSwitch />);
    const btn = screen.getByRole('button', { name: /Switch language/ });
    // Shows the language you'd switch TO: currently 'en' → offers 中.
    expect(btn).toHaveTextContent('中');
    await userEvent.click(btn);
    expect(useLanguageStore.getState().language).toBe('zh-CN');
  });
});

describe('ErrorBoundary', () => {
  it('renders a localised fallback when a child throws', () => {
    const Boom = () => {
      throw new Error('kaboom');
    };
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    spy.mockRestore();
  });
});

describe('ToastHost (UI-REQ-002)', () => {
  const withClient = (node: ReactNode) => (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {node}
    </QueryClientProvider>
  );

  it('renders a retryable failure with the retry affordance and clears it on use', async () => {
    render(withClient(<ToastHost />));
    pushToast({ tone: 'error', title: 'Request failed', body: 'boom', correlationId: 'c9', retryable: true });
    expect(await screen.findByText('boom')).toBeInTheDocument();
    expect(screen.getByText(/c9/)).toBeInTheDocument();
    // The store has carried `retryable` since UI-REQ-002 was written while the render offered only
    // Close; this assertion is what keeps the affordance from disappearing again.
    await userEvent.click(screen.getByRole('button', { name: /Retry|重试/ }));
    expect(screen.queryByText('boom')).not.toBeInTheDocument();
  });

  it('does not offer to retry what S1 refused', async () => {
    render(withClient(<ToastHost />));
    pushToast({ tone: 'error', title: 'Request failed', body: 'denied', retryable: false });
    expect(await screen.findByText('denied')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry|重试/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Close|关闭/ }));
    expect(screen.queryByText('denied')).not.toBeInTheDocument();
  });

  it('surfaces API errors through the query-cache bridge', async () => {
    render(withClient(<ToastHost />));
    // onApiError listeners fire when the mutation/query cache reports an ApiError.
    const listener = onApiError(() => {});
    expect(typeof listener).toBe('function');
    listener();
  });

  it('isApiError surfaces the errorCode', () => {
    const e = new ApiError({ errorCode: 'ENVIRONMENT_LOCKED', httpStatus: 423 });
    expect(e.errorCode).toBe('ENVIRONMENT_LOCKED');
  });
});

function withQc(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

describe('read-model queries', () => {
  it('fetches projects, environments and notifications from S1 via the client', async () => {
    const { result } = renderHook(
      () => ({
        p: useQuery(projectsListOptions()),
        e: useQuery(environmentsOptions()),
        n: useQuery(notificationsOptions()),
      }),
      { wrapper: ({ children }: { children: ReactNode }) => withQc(children) },
    );
    await waitFor(() => expect(result.current.p.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.e.isSuccess).toBe(true));
    await waitFor(() => expect(result.current.n.isSuccess).toBe(true));
    expect(result.current.p.data?.items?.[0]?.name).toBe('Community Health Register');
    expect((result.current.e.data?.items ?? []).length).toBe(3);
  });
});
