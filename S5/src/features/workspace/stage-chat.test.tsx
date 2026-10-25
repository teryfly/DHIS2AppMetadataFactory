/*
 * UI-WB-010 (AI-CHAT-001~003, FR-UI-002): the stage's Review Assistant.
 *
 * The panel is rendered directly rather than through a whole route: what is under test is that it
 * asks S1, shows what S1 answered, and refuses to offer a turn the server has already refused —
 * none of which depends on the rest of the workbench.
 */
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { StageChatPanel, hasDialogue } from './StageChatPanel';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';
import { useOperatorStore } from '@/stores/operator';

const P = '/api/v1';

function renderPanel(stageId: string) {
  return render(
    <AppProviders>
      <MemoryRouter>
        <StageChatPanel stageId={stageId} workflowId="WF-1" />
      </MemoryRouter>
    </AppProviders>,
  );
}

describe('the stage conversation (UI-WB-010)', () => {
  beforeEach(() => {
    useOperatorStore.getState().setName('ada');
    db.chatTranscripts['ST-req'] = [];
  });

  afterEach(() => {
    cleanup();
  });

  it('shows the context header S1 attached, not one assembled in the browser', async () => {
    renderPanel('ST-req');
    // the header is only trustworthy once S1's answer has arrived, so wait for the value itself
    expect(await screen.findByText(/env-dev/)).toBeInTheDocument();
    expect(screen.getByText(/Target environment/)).toBeInTheDocument();
  });

  it('sends a turn and renders the answer with the questions it raised', async () => {
    server.use(
      http.post(`${P}/stages/:stageId/chat`, () =>
        HttpResponse.json({
          stageId: 'ST-req',
          context: { projectId: db.DESIGN_PROJECT_ID, targetEnvironments: ['env-dev'] },
          answer: 'Noted for ST-req: Which ward list?',
          patchIds: ['PATCH-7'],
          questions: [{ localId: 'Q-1', text: 'Should the header row be treated as data?', severity: 'WARNING', blocking: false }],
        }),
      ),
    );
    renderPanel('ST-req');
    await userEvent.type(screen.getByLabelText(/Ask about this stage/), 'Which ward list?');
    // re-read the control after typing: the panel re-renders on every keystroke, and a click on the
    // node captured before it is a click on a detached element
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }));

    expect(await screen.findByText(/Noted for ST-req/)).toBeInTheDocument();
    // AI-CHAT-001: a dialogue question is advice; it does not block a Gate
    expect(screen.getByText(/Should the header row be treated as data\?/)).toBeInTheDocument();
    expect(screen.getByText(/not a Gate blocker/)).toBeInTheDocument();
  });

  it('keeps a retried identical turn on a real idempotency key', async () => {
    const seen: string[] = [];
    server.use(
      http.post(`${P}/stages/:stageId/chat`, ({ request }) => {
        seen.push(request.headers.get('Idempotency-Key') ?? '');
        return HttpResponse.json({ stageId: 'ST-req', context: {}, answer: 'ok', patchIds: [], questions: [] });
      }),
    );
    renderPanel('ST-req');
    await screen.findByLabelText(/Ask about this stage/);
    await userEvent.type(screen.getByLabelText(/Ask about this stage/), 'same text');
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }));
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0]).toBeTruthy();
  });

  it('offers no turn for a stage S1 has blocked', async () => {
    let reached = 0;
    server.use(
      http.post(`${P}/stages/:stageId/chat`, () => {
        reached += 1;
        return HttpResponse.json({ stageId: 'ST-page', context: {}, answer: 'no', patchIds: [], questions: [] });
      }),
    );
    renderPanel('ST-page');
    await screen.findByLabelText(/Ask about this stage/);
    const ask = screen.getByRole('button', { name: 'Ask' });
    // FR-UI-009 / UI-BR-01: `stage.chat` is unavailable for a SKIPPED stage, so there is no way to
    // send — and the panel does not send anything and hope the server refuses.
    expect(ask).toBeDisabled();
    await userEvent.click(ask);
    expect(reached).toBe(0);
  });
});

/*
 * AI-CHAT-001 draws the dialogue at the design line, and both S1 (`domain/enums.py`) and S3
 * (`_design_stage`) refuse the read beyond it. The workbench mounts the panel from this predicate,
 * so a workflow stopped at DISCOVERY asks for nothing rather than toasting 对象不存在.
 */
describe('which stages hold a dialogue (AI-CHAT-001)', () => {
  it.each([
    ['REQUIREMENTS', true],
    ['DOMAIN', true],
    ['MAPPING', true],
    ['PAGE_DESIGN', true],
    ['DISCOVERY', false],
    ['BASELINE_COMPILE', false],
    ['HANDOFF', false],
    [undefined, false],
  ])('%s → %s', (stageType, expected) => {
    expect(hasDialogue(stageType as string | undefined)).toBe(expected);
  });
});
