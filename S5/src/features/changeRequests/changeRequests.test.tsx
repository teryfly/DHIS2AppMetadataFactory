/*
 * UI-OPS-001: the steps a person may take on a change request are S1's answer per request, not
 * an inference from the status string on screen. Two requests in two states make the difference
 * visible: the analysed one can be approved, the approved one can be started, and neither offers
 * what its state does not allow.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { DESIGN_PROJECT_ID } from '@/mocks/db';
import { server } from '@/mocks/server';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

/**
 * The card that holds a given request, found by walking up from its reason until the step buttons
 * are inside — the list order is not something a test should depend on.
 */
async function cardFor(reason: string): Promise<HTMLElement> {
  const title = await screen.findByText(reason);
  let node: HTMLElement | null = title.parentElement;
  while (node && !node.querySelector('button')) node = node.parentElement;
  if (!node) throw new Error(`no card rendered for ${reason}`);
  return node;
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('Change request steps (UI-OPS-001)', () => {
  it('offers each request only the steps its own state allows', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/change-requests`);
    const analysed = await cardFor('Add birth-weight aggregation');
    const approved = await cardFor('Rename the tally page');

    await waitFor(() => expect(within(analysed).getByRole('button', { name: 'Approve' })).toBeEnabled());
    expect(within(analysed).getByRole('button', { name: 'Start' })).toBeDisabled();
    expect(within(approved).getByRole('button', { name: 'Start' })).toBeEnabled();
    // S1 said nothing about completing either request, so it stays closed rather than guessed
    expect(within(approved).getByRole('button', { name: 'Complete' })).toBeDisabled();
  });

});
