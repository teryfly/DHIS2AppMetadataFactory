/*
 * UI-SET-001 … 005. The page is thin; what carries the requirement is the mapping from S1's facts
 * to a step's state, so most of this file tests that mapping directly and only then renders it.
 * The rule under test is UI-BR-02: the console may report what S1 said, never what it supposes.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import userEvent from '@testing-library/user-event';
import i18n from '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { capabilityFacts, setupSteps } from './SetupView';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';
import type { SetupView } from '@/api/types';

const OPEN = { available: true, reason: '' };

function facts(patch: Partial<SetupView> = {}): SetupView {
  return { ...db.setupView(), ...patch } as SetupView;
}

function stateOf(id: string, input: SetupView, create = OPEN): string | undefined {
  return setupSteps(input, create).find((step) => step.id === id)?.state;
}

describe('setupSteps — the facts → state mapping', () => {
  it('says nothing is configured on a machine with no environments and no models', () => {
    const bare = facts({
      environments: { total: 0, inService: 0, nonProductionInService: 0, neverTested: [], items: [] },
      intelligence: { providers: 0, providersEnabled: 0, models: 0, modelsEnabled: 0 },
      projects: { total: 0, active: 0 },
    });
    expect(stateOf('environments', bare)).toBe('missing');
    expect(stateOf('models', bare)).toBe('missing');
    expect(stateOf('project', bare)).toBe('missing');
  });

  it('does not count a stand-in capability as wired up', () => {
    // reported as a fact, not as a step: `provider_mode` is chosen in the server's env file, so the
    // checklist has nothing for an operator to complete here
    const seen = capabilityFacts(facts({ providers: { S2: 'real', S3: 'fake', S4: 'real' } }));
    expect(seen.ready).toBe(false);
    // The port labels the server sends (`S2`/`S3`/`fake`) are not words the operator can act on;
    // what reaches the screen is the capability name.
    expect(seen.which).toBe('Requirement parsing');
    expect(seen.mode).toBe('Blueprint compilation: live · Requirement parsing: demo stand-in · Environment execution: live');
  });

  it('keeps a label it has never seen readable, because the contract does not bound this map', () => {
    // `providers` is `{[key: string]: string}`; a deployment can arrive with a port name or a mode
    // this console has never heard of. Blank would read as "no capability" instead of "unrecognised".
    const seen = capabilityFacts(facts({ providers: { 'openai-compatible': 'weird' } }));
    expect(seen.mode).toBe('openai-compatible: weird');
    expect(seen.which).toBe('openai-compatible');
  });

  it('names the capabilities in the language the operator is reading', async () => {
    // The facts block is rebuilt on every render, so a label resolved once in English and kept after
    // a switch would show a Chinese operator an English capability name.
    const input = facts({ providers: { S2: 'real', S3: 'fake', S4: 'real' } });
    expect(capabilityFacts(input).which).toBe('Requirement parsing');
    await i18n.changeLanguage('zh-CN');
    expect(capabilityFacts(input).which).toBe('需求解析');
    expect(capabilityFacts(input).mode).toBe('蓝图编译：真实接入 · 需求解析：演示替身 · 环境执行：真实接入');
    await i18n.changeLanguage('en');
  });

  it('keeps an environment that never passed a connection test out of "ready"', () => {
    const input = facts({
      environments: {
        total: 1,
        inService: 1,
        nonProductionInService: 1,
        neverTested: ['env-dev'],
        items: [{ environmentKey: 'env-dev', environmentClass: 'TEST', status: 'ACTIVE', lastTestedAt: null }],
      },
    });
    const step = setupSteps(input, OPEN).find((item) => item.id === 'environments')!;
    expect(step.state).toBe('partial');
    expect(step.consequence.key).toBe('setup.environmentsUntested');
  });

  it('refuses to call production-only service "ready": a project still cannot be created', () => {
    const input = facts({
      environments: {
        total: 1,
        inService: 1,
        nonProductionInService: 0,
        neverTested: [],
        items: [{ environmentKey: 'env-prod', environmentClass: 'PRODUCTION', status: 'ACTIVE', lastTestedAt: '2026-09-30T08:00:00Z' }],
      },
    });
    const step = setupSteps(input, OPEN).find((item) => item.id === 'environments')!;
    expect(step.state).toBe('partial');
    expect(step.consequence.key).toBe('setup.environmentsNoNonProd');
  });

  it('shows a withheld governance section as "not visible to you", never as zero models', () => {
    const hidden = facts({ intelligence: null });
    const step = setupSteps(hidden, OPEN).find((item) => item.id === 'models')!;
    expect(step.state).toBe('unknown');
    expect(step.fact.key).toBe('setup.modelsHidden');
    expect(JSON.stringify(step)).not.toContain('0 model');
  });

  it('reports the project step through S1’s answer, not through the count', () => {
    const denied = facts({ projects: { total: 0, active: 0 } });
    const step = setupSteps(denied, { available: false, reason: 'You lack the right.' }).find((item) => item.id === 'project')!;
    expect(step.state).toBe('unknown');
    expect(step.consequence.key).toBe('setup.projectsDenied');
    expect(step.consequence.options).toEqual({ reason: 'You lack the right.' });
  });

  it('still counts an existing project as existing when create is refused', () => {
    // availability answers what may be done; it does not rewrite what has been done (UI-BR-01)
    const withProject = facts({ projects: { total: 2, active: 1 } });
    const step = setupSteps(withProject, { available: false, reason: 'You lack the right.' }).find((item) => item.id === 'project')!;
    expect(step.state).toBe('ready');
    expect(step.consequence.key).toBe('setup.projectsReady');
    expect(step.to).toBe('/projects');
  });

  it('links the pipeline step at a project once one exists', () => {
    const withProject = facts({ projects: { total: 1, active: 1 } });
    const step = setupSteps(withProject, OPEN).find((item) => item.id === 'pipeline')!;
    expect(step.to).toBe('/projects');
  });
});

describe('the getting-started page', () => {
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
  });
  afterEach(() => server.resetHandlers());

  it('stands on the first unfinished step and opens its form without leaving the page', async () => {
    renderAt('/getting-started');
    // every step is named, and the one the operator should be working on is the open heading
    expect(await screen.findByRole('button', { name: /Model providers and models/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /DHIS2 environments/i })).toBeInTheDocument();
    // the mock has an in-service environment that never passed a test, which is the branch the
    // page exists to surface
    await waitFor(() => expect(screen.getAllByText(/Partly done/i).length).toBeGreaterThan(0));
    expect(screen.getByText(/env-dev/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Register an environment/i }));
    expect(await screen.findByLabelText(/^Environment key/i)).toBeInTheDocument();
    // the credential control belongs inside this form (S5IF-SEC01): finishing the step must not
    // require a trip to another page, and it must not require an env var the operator cannot set
    expect(screen.getAllByRole('button', { name: /Enter a new credential/i }).length).toBeGreaterThan(0);
    // the Administration page stays one click away for anyone who would rather work there
    expect(screen.getByRole('link', { name: /open the Administration page/i })).toBeInTheDocument();
  });

  it('does not claim a step is finished because a form was opened', async () => {
    renderAt('/getting-started');
    const before = await screen.findAllByText(/Partly done/i);
    await userEvent.click(screen.getByRole('button', { name: /Register an environment/i }));
    await userEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    // closing a form writes nothing, so the state on screen has to be exactly what it was
    expect(screen.getAllByText(/Partly done/i).length).toBe(before.length);
  });

  it('states the capability wiring as a fact, with nothing to click', async () => {
    // `GET /setup.providers` is whether the three capability ports are real or stand-ins — a
    // deployment choice made in the server's own env file. It used to be a checklist step, which
    // asked the operator to complete something no control in a browser can change.
    server.use(
      http.get('/api/v1/setup', () => HttpResponse.json(facts({ providers: { S2: 'real', S3: 'fake', S4: 'real' } }))),
    );
    renderAt('/getting-started');
    const heading = await screen.findByRole('heading', { name: /^Capabilities$/i });
    const block = heading.closest('section')!;
    expect(block.textContent).toContain('Requirement parsing');
    expect(within(block).queryAllByRole('button')).toHaveLength(0);
  });

  it('shows configuration problems in S1’s own words when there are any', async () => {
    server.use(
      http.get('/api/v1/setup', () =>
        HttpResponse.json(
          facts({
            configuration: {
              providerMode: 'auto',
              workflowEngine: 'inprocess',
              migrationsMode: 'apply',
              httpsOnly: false,
              authMode: 'local',
              problems: ["FACTORY_HTTPS_ONLY must be true when FACTORY_AUTH_MODE is not 'off'"],
            },
          }),
        ),
      ),
    );
    renderAt('/getting-started');
    expect(await screen.findByText(/FACTORY_HTTPS_ONLY must be true/)).toBeInTheDocument();
  });
});
