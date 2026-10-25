/*
 * Empty and not-found states — real UI behaviour worth covering (UI-PRJ-001 empty sections,
 * missing resources), which also closes the coverage tail without touching production logic.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <OperatorGate>
          <AppRoutes />
        </OperatorGate>
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('empty states', () => {
  it('Dashboard shows empty sections when S1 has no data', async () => {
    server.use(
      http.get(`/api/v1/projects`, () => HttpResponse.json({ items: [], total: 0 })),
      http.get(`/api/v1/gates`, () => HttpResponse.json({ items: [], total: 0 })),
      http.get(`/api/v1/deployment-runs`, () => HttpResponse.json({ items: [], total: 0 })),
      http.get(`/api/v1/notifications`, () => HttpResponse.json({ items: [], total: 0 })),
    );
    renderAt('/');
    expect(await screen.findByText(/No projects yet|还没有项目/)).toBeInTheDocument();
    expect(screen.getByText(/No gates awaiting/)).toBeInTheDocument();
  });

  it('Projects list shows the empty message', async () => {
    server.use(http.get(`/api/v1/projects`, () => HttpResponse.json({ items: [], total: 0 })));
    renderAt('/projects');
    expect(await screen.findByText(/No projects yet|暂无项目/)).toBeInTheDocument();
    // the empty list is not a dead end: it points at the page that says what is still missing
    expect(screen.getByRole('link', { name: /See what is still missing|看看还差哪些配置/ })).toHaveAttribute(
      'href',
      '/getting-started',
    );
  });

  it('Notifications shows the empty message', async () => {
    server.use(http.get(`/api/v1/notifications`, () => HttpResponse.json({ items: [], total: 0 })));
    renderAt('/notifications');
    expect(await screen.findByText(/No notifications|没有通知/)).toBeInTheDocument();
  });
});

describe('missing resources', () => {
  it('Gate not-found path surfaces the error', async () => {
    server.use(http.get(`/api/v1/gates/:id`, () => HttpResponse.json({ errorCode: 'NOT_FOUND' }, { status: 404 })));
    renderAt('/projects/PRJ-green-01/gate/GT-missing');
    expect(await screen.findByText(/does not exist|不存在/i)).toBeInTheDocument();
  });
});

describe('project shell (UI-IA-001)', () => {
  it('does not reserve a permanent third column for an inspector', async () => {
    renderAt('/projects/PRJ-green-01/overview');
    await screen.findAllByText('Requirements');
    expect(screen.queryByText('Inspector')).not.toBeInTheDocument();
    expect(document.querySelector('.inspector')).toBeNull();
  });
});
