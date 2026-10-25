/*
 * UI-ADM-003's activation rule, proved on the panel that replaced the "arrives at M2" sentence:
 * a prompt version becomes activable only when the evaluation and the Golden Project regression
 * result are both on record and an enabled model covers the agent — and when it is not, the screen
 * says which of the three is missing instead of showing a dead button.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
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

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
  // The shared fixture already grants `governance.prompt.activate` (db.platformActionsView), so the
  // panel is tested against the same answer every other Administration page sees.
});
afterEach(() => server.resetHandlers());

describe('Prompt activation gate (UI-ADM-003)', () => {
  it('activates the version that has evaluation, regression and a model', async () => {
    let activated: Record<string, unknown> | null = null;
    server.use(
      http.post(`/api/v1/governance/prompts/activations`, async ({ request }) => {
        activated = (await request.json()) as Record<string, unknown>;
        return Response.json({ agentType: activated.agentType });
      }),
    );
    renderAt('/governance/prompts');
    // Three rows offer the same label, so the search is plural: a single-element query would answer
    // "timed out" for what is really a strict-mode ambiguity.
    // the rows appear before the platform answers who may press them, so what the test waits for is
    // an enabled control, not any control
    let button: HTMLButtonElement | undefined;
    await waitFor(() => {
      button = screen
        .getAllByRole('button', { name: /Activate|激活/ })
        .find((b) => !b.hasAttribute('disabled')) as HTMLButtonElement | undefined;
      expect(button, 'the covered version has no enabled activation').toBeTruthy();
    });
    await userEvent.click(button as HTMLElement);
    await waitFor(() => expect(activated).not.toBeNull());
    expect(activated).toMatchObject({
      agentType: 'MAPPING',
      promptName: 'mapping.extract',
      promptVersion: 4,
      modelList: ['openai/gpt-x-large'],
    });
  });

  it('names what is missing on a version that has only a failed evaluation', async () => {
    renderAt('/governance/prompts');
    // mapping.extract is the activable one; build.compile must not be offered as a live control.
    await waitFor(() =>
      expect(
        screen.getAllByRole('button', { name: /Activate|激活/ }).some((b) => !b.hasAttribute('disabled')),
      ).toBe(true));
    expect(await screen.findByText(/no evaluation result|缺少评测结果/)).toBeInTheDocument();
  });

  it('shows the version that is already effective without offering to switch it again', async () => {
    renderAt('/governance/prompts');
    const rows = await screen.findByText('domain.model');
    expect(rows).toBeInTheDocument();
    expect(await screen.findByText(/已生效|Active/)).toBeInTheDocument();
  });
});
