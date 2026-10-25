/*
 * Deeper interaction coverage: wizard to review, environment create form (Secret-reference-only —
 * AC-06), the COMMIT second-confirmation modal (UI-DEP-004), project settings actions, and the
 * inspector selection. All via the real router + MSW.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
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
  useUiStore.setState({ selectedObjectKey: null });
});

describe('Wizard full walk to review (UI-PRJ-002)', () => {
  it('collects basics → owner → delivery → language → targets → review', async () => {
    renderAt('/projects/new');
    // The wizard is a lazy route chunk now (UI-NFR-06), so each step has to be awaited rather than
    // assumed present in the tick after the click: the chunk arrives, then the step's own read does.
    const next = async () => {
      await userEvent.click(await screen.findByRole('button', { name: /Next|Finish/ }));
    };
    await screen.findAllByRole('textbox');
    await userEvent.type(screen.getAllByRole('textbox')[0] as HTMLInputElement, 'My Project');
    await next();
    await screen.findAllByRole('textbox');
    await userEvent.type(screen.getAllByRole('textbox')[0] as HTMLInputElement, 'Owner Name');
    await next();
    await next(); // delivery (defaults set)
    await next(); // language
    // targets step: add the first environment option
    const addBtns = await screen.findAllByRole('button', { name: /Add environment/ });
    if (addBtns.length) await userEvent.click(addBtns[0] as HTMLElement);
    await userEvent.click(await screen.findByRole('button', { name: /Finish|Next/ }));
    // Review step shows the summary + create button.
    expect(await screen.findByText(/Review your project/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Create project/ })).toBeInTheDocument();
  });
});

// The environment form's Secret-reference rule moved to features/governance/governance.test.tsx:
// it needs the platform allowed-actions answer to land before the trigger opens, and that
// harness lives with the other Administration-page tests.

describe('Project settings (UI-PRJ-004)', () => {
  it('renders targets, design-reference warning and export/archive actions', async () => {
    renderAt('/projects/PRJ-green-01/settings');
    expect(await screen.findByText(/Changing the design reference/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Export/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Archive/ })).toBeInTheDocument();
  });
});

