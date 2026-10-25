import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useUiStore } from '@/stores/ui';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  useOperatorStore.getState().setName('Ana');
  useUiStore.getState().closeHelp();
});

describe('global help (UI-IA-001)', () => {
  it('opens the workflow map from the shell and closes on Escape', async () => {
    renderAt('/');
    const helpButtons = await screen.findAllByRole('button', { name: /^Help$|^帮助$/ });
    await userEvent.click(helpButtons[0]!);

    const dialog = await screen.findByRole('dialog', { name: /Help and workflow|帮助与流程/ });
    expect(within(dialog).getByText(/The path from materials|从材料到已验证交付/)).toBeInTheDocument();
    expect(within(dialog).getAllByText(/Approval gate|审批节点/).length).toBeGreaterThan(0);

    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens the worked example from the help drawer', async () => {
    renderAt('/');
    const helpButtons = await screen.findAllByRole('button', { name: /^Help$|^帮助$/ });
    await userEvent.click(helpButtons[0]!);

    const dialog = await screen.findByRole('dialog', { name: /Help and workflow|帮助与流程/ });
    await userEvent.click(within(dialog).getByRole('link', { name: /Open the walkthrough|打开完整示例/ }));

    expect(await screen.findByRole('heading', { name: /Walk through a real example|用一个实例走完整流程/ })).toBeInTheDocument();
    expect((await screen.findAllByText(/出院随访\.txt/)).length).toBeGreaterThan(0);
  });
});
