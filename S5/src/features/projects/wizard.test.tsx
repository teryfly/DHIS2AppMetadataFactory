/*
 * UI-PRJ-002 with UI-SET-004: the wizard has to say why it will not advance. An earlier version
 * raised a validation error that was rendered nowhere, so with zero registered environments the
 * Next button simply did nothing and the page looked broken.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
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

/** The step counter in the heading is the only unambiguous "which step am I on" the page offers. */
const stepHeading = (name: string, index: number) => new RegExp(`${name} · ${index}/6`);

/**
 * Walk the wizard by clicking whatever its forward control is called on the current step — the last
 * step before Review reads "Finish", and a step that refuses to advance has to be detected, not
 * assumed.
 */
async function walkTo(name: string, index: number, attempts = 8) {
  for (let i = 0; i < attempts; i += 1) {
    if (screen.queryByText(stepHeading(name, index))) return;
    const forward = screen.queryByRole('button', { name: /^(Next|Finish)$/ });
    if (!forward) throw new Error(`no forward button while looking for ${name}`);
    await userEvent.click(forward);
  }
  throw new Error(`never reached ${name} · ${index}/6`);
}

async function advanceToEnvironmentStep(rows: Record<string, unknown>[]) {
  server.use(
    http.get('/api/v1/environments', () => HttpResponse.json({ items: rows, total: rows.length, limit: 50, offset: 0 })),
  );
  renderAt('/projects/new');
  await userEvent.type(await screen.findByLabelText(/Project name/i), 'Demo');
  await walkTo('Environments', 5);
}

/** Click the wizard's forward control whatever it is called on this step. */
async function forward() {
  const control = screen.queryByRole('button', { name: /^(Next|Finish)$/ });
  if (!control) throw new Error('no forward button');
  await userEvent.click(control);
  return control;
}

const production = {
  environmentKey: 'env-only',
  name: 'Only production',
  baseUrl: 'https://only.invalid',
  environmentClass: 'PRODUCTION',
  status: 'ACTIVE',
  authMode: 'PAT',
  tlsRequired: true,
  timeoutSeconds: 60,
  llmDataPolicy: 'PRIVATE_NETWORK',
  versionConstraint: '2.41.x',
  credentials: [],
  secretRefs: { discovery: 'vault://only/d', import: 'vault://only/i' },
};

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('the environment step (UI-SET-004)', () => {
  it('names the missing prerequisite instead of refusing to advance silently', async () => {
    await advanceToEnvironmentStep([]);
    expect(await screen.findByText(/No DHIS2 environment is registered yet/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Register an environment first/i })).toHaveAttribute('href', '/environments');

    await forward();
    expect(await screen.findByRole('alert')).toHaveTextContent(/at least one target environment/i);
    // and it is still the same step
    expect(screen.getByText(stepHeading('Environments', 5))).toBeInTheDocument();
  });

  it('refuses a project that would target production only', async () => {
    await advanceToEnvironmentStep([production]);
    await userEvent.click(await screen.findByRole('button', { name: /Add environment/i }));
    await forward();
    // the message states the rule S1 enforces too (CP-PRJ-002), so the two cannot diverge
    expect(await screen.findByRole('alert')).toHaveTextContent(/non-production/i);
  });

  it('keeps exactly one design reference when a second environment is chosen as it', async () => {
    const captured: Record<string, unknown>[] = [];
    const alpha = { ...production, environmentKey: 'env-alpha', environmentClass: 'TEST', name: 'Alpha' };
    const beta = { ...production, environmentKey: 'env-beta', environmentClass: 'TEST', name: 'Beta' };
    server.use(
      http.post('/api/v1/projects', async ({ request }) => {
        captured.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: 'prj-new', name: 'Demo', stages: [] }, { status: 201 });
      }),
    );
    await advanceToEnvironmentStep([alpha, beta]);
    // each unselected row carries its own "Add environment"; taking the first twice adds both
    await userEvent.click((await screen.findAllByRole('button', { name: /Add environment/i }))[0]!);
    await userEvent.click((await screen.findAllByRole('button', { name: /Add environment/i }))[0]!);
    // choosing the second as the design reference moves it, it does not add a second one
    const boxes = screen.getAllByRole('checkbox', { name: /Design reference/i });
    await userEvent.click(boxes[1]!);
    await forward();
    await userEvent.click(await screen.findByRole('button', { name: /Create project/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    const rows = (captured[0]?.targetEnvironments ?? []) as { environmentKey: string; isDesignReference: boolean }[];
    expect(rows.map((row) => row.environmentKey).sort()).toEqual(['env-alpha', 'env-beta']);
    expect(rows.filter((row) => row.isDesignReference)).toHaveLength(1);
  });
});

describe('the language step (UI-PRJ-002)', () => {
  it('collects translation languages and sends them', async () => {
    const captured: Record<string, unknown>[] = [];
    server.use(
      http.get('/api/v1/environments', () =>
        HttpResponse.json({
          items: [{ ...production, environmentKey: 'env-dev', environmentClass: 'TEST', name: 'Dev' }],
          total: 1,
          limit: 50,
          offset: 0,
        }),
      ),
      http.post('/api/v1/projects', async ({ request }) => {
        captured.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ id: 'prj-new', name: 'Demo', stages: [] }, { status: 201 });
      }),
    );

    renderAt('/projects/new');
    await userEvent.type(await screen.findByLabelText(/Project name/i), 'Demo');
    await forward();
    await forward();
    await forward();

    // move the default off `en`, which is what makes English collectable as a translation
    await userEvent.selectOptions(await screen.findByLabelText(/Default language/i), 'zh-CN');
    await userEvent.click(screen.getByRole('checkbox', { name: 'English' }));
    await forward();

    await userEvent.click(await screen.findByRole('button', { name: /Add environment/i }));
    await forward();
    await userEvent.click(await screen.findByRole('button', { name: /Create project/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toMatchObject({ name: 'Demo', defaultLanguage: 'zh-CN', translationLanguages: ['en'] });
  });
});
