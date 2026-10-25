/*
 * Route-level integration: mount the real provider tree + router at each IA route against the MSW
 * mock (UI-NFR-05), which exercises AppShell, TopBar, routing, the queries and the feature views in
 * one pass. Fixture data comes from src/mocks so assertions key off known content.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useUiStore } from '@/stores/ui';

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
  useOperatorStore.getState().setName('Ana Ruiz');
  useUiStore.getState().closeOperatorDialog();
});

describe('Dashboard (UI-PRJ-001)', () => {
  it('shows seeded projects, the pending-gate section and notifications', async () => {
    renderAt('/');
    expect((await screen.findAllByText('Community Health Register')).length).toBeGreaterThan(0);
    // the second project now appears twice on this screen — as a project card and as the owner of
    // the deployment gate that is waiting on a decision — so name the count rather than one element
    expect((await screen.findAllByText('ANC Tally Migration')).length).toBeGreaterThan(1);
    expect(screen.getByText(/Gates awaiting approval/)).toBeInTheDocument();
  });
});

describe('Projects list & wizard (UI-PRJ-001/002)', () => {
  it('lists projects and toggles archived', async () => {
    renderAt('/projects');
    expect(await screen.findByText('Community Health Register')).toBeInTheDocument();
    const toggle = screen.getByRole('checkbox', { name: /Show archived/ });
    await userEvent.click(toggle);
    expect((toggle as HTMLInputElement).checked).toBe(true);
  });

  it('advances the create wizard past validation', async () => {
    renderAt('/projects/new');
    await screen.findByText('New project');
    const nameInput = screen.getAllByRole('textbox')[0] as HTMLInputElement;
    await userEvent.type(nameInput, 'New Thing');
    await userEvent.click(screen.getByRole('button', { name: /Next/ }));
    // On the owner step now: type a name and advance to delivery.
    const ownerInput = screen.getAllByRole('textbox')[0] as HTMLInputElement;
    await userEvent.type(ownerInput, 'Sam');
    await userEvent.click(screen.getByRole('button', { name: /Next/ }));
    expect((await screen.findAllByText(/Delivery mode/)).length).toBeGreaterThan(0);
  });
});

describe('Project workspace (UI-WB)', () => {
  it('renders the workflow stepper and the five primary sections (AC-WF-01 skips)', async () => {
    renderAt('/projects/PRJ-green-01/overview');
    expect((await screen.findAllByText('Requirements')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Domain model/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Skipped/)).length).toBeGreaterThan(0);
    for (const label of ['Overview', 'Design', 'Results', 'Delivery', 'Manage']) {
      expect(screen.getAllByRole('link', { name: label }).length).toBeGreaterThan(0);
    }
  }, 15000);

  it('stage view shows the action bar and the disclosed manual authoring path', async () => {
    renderAt('/projects/PRJ-green-01/stages/ST-req');
    const disclosure = await screen.findByRole('button', { name: /Manual authoring/i });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(disclosure);
    expect(await screen.findByText('Structured form')).toBeInTheDocument();
  }, 15000);

  it('no longer renders a permanent inspector', async () => {
    renderAt('/projects/PRJ-green-01/overview');
    expect((await screen.findAllByText('Requirements')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Inspector')).not.toBeInTheDocument();
  }, 15000);
});

describe('Gate page (UI-GATE)', () => {
  it('blocks approve until warnings marked, per allowed-actions (AC-04)', async () => {
    renderAt('/projects/PRJ-green-01/gate/GT-1');
    await screen.findByText(/Warnings/);
    const approve = screen.getByRole('button', { name: /Approve/ }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true); // gate.approve is unavailable in allowed-actions
    for (const b of screen.getAllByRole('button', { name: /Accept/ })) await userEvent.click(b);
    expect(approve.disabled).toBe(true); // still authoritative on allowed-actions
  });

  it('reject requires a reason (UI-GATE-004)', async () => {
    renderAt('/projects/PRJ-green-01/gate/GT-1');
    expect(await screen.findByText(/Reason for rejection/)).toBeInTheDocument();
  });
});

describe('Deployments & Run console (UI-DEP)', () => {
  it('lists runs by environment', async () => {
    renderAt('/projects/PRJ-green-01/deployments');
    expect(await screen.findByText(/env-dev/)).toBeInTheDocument();
  });

  it('run console renders summary + COMMIT', async () => {
    renderAt('/projects/PRJ-green-01/deployments/RUN-1');
    expect(await screen.findByText(/Deployment console/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'COMMIT' })).toBeInTheDocument();
  });
});

describe('Admin screens (UI-ADM/OPS)', () => {
  it('environments list (AC-06 secret refs only)', async () => {
    renderAt('/environments');
    expect(await screen.findByText('Dev sandbox')).toBeInTheDocument();
    expect((await screen.findAllByText(/Production/)).length).toBeGreaterThan(0);
  });

  it('governance models table', async () => {
    renderAt('/governance');
    expect(await screen.findByText('gpt-x-large')).toBeInTheDocument();
  });

  it('notifications list', async () => {
    renderAt('/notifications');
    expect(await screen.findByText(/awaiting approval/)).toBeInTheDocument();
  });

  it('system settings form', async () => {
    renderAt('/settings');
    expect(await screen.findByText(/Idempotency retention/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save settings/ })).toBeInTheDocument();
  });

  it('audit view renders filters', async () => {
    renderAt('/projects/PRJ-green-01/audit');
    expect(await screen.findByText(/Verify chain/)).toBeInTheDocument();
  });
});

describe('Top bar (UI-OP-001)', () => {
  it('shows the current operator and reopens the dialog', async () => {
    renderAt('/');
    const bar = document.querySelector('.topbar') as HTMLElement;
    const chip = within(bar).getByText(/Ana Ruiz/);
    await userEvent.click(chip);
    expect(await screen.findByText(/Enter your name/)).toBeInTheDocument();
  });
});
