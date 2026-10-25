/*
 * S5IF-INP08/INP09: one input's model conversation. The list is summaries only; expanding a
 * card is what fetches the request and the raw answer, which is why the interaction is part of
 * the test rather than a preloaded fixture assertion.
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
import { DESIGN_PROJECT_ID } from '@/mocks/db';

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

describe('The LLM conversation log (S5IF-INP08)', () => {
  it('lists every attempt with its status and fetches the request on expand', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs/INP-1/llm-log`);

    expect(await screen.findByText('LLM conversation')).toBeInTheDocument();
    expect(await screen.findByText('Answered')).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('1.2 s')).toBeInTheDocument();
    expect(screen.getByText('120 in / 40 out')).toBeInTheDocument();

    const show = screen.getAllByRole('button', { name: /Show conversation/i });
    await userEvent.click(show[0]!);

    expect(await screen.findByText('system rules')).toBeInTheDocument();
    expect(await screen.findByText('<untrusted kind="INPUT_ARTIFACT">a,b</untrusted>')).toBeInTheDocument();
    expect(await screen.findByText('{"items": []}')).toBeInTheDocument();
  });

  it('shows the failure of an attempt without needing to expand it', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs/INP-1/llm-log`);

    expect(
      await screen.findByText('LLM_TIMEOUT: model request timed out after 300.0s'),
    ).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });
});
