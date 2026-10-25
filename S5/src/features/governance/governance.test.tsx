/*
 * FR-UI-009 / UI-BR-01 on the Administration pages: the answer to "may this control be used"
 * comes from S1's platform-scope allowed-actions (read live from S4), never from what the page can
 * guess about the row it is rendering. A production emergency lock decided in the browser is the
 * exact failure the rule exists to prevent.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { EnvironmentForm } from '@/features/environments/EnvironmentsView';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

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

describe('Platform gating on the Administration pages (FR-UI-009)', () => {
  it('enables what S1 allows, closes what it refuses, and guesses nothing', async () => {
    let asked = 0;
    server.use(
      http.get(`/api/v1/allowed-actions`, () => {
        asked += 1;
        return HttpResponse.json(db.platformActionsView());
      }),
    );
    renderAt('/environments');
    await screen.findByText('Dev sandbox');

    // row-scoped: each environment has its own answer, and the page must not average them
    // matched on the name cell specifically: the class column says "Staging" too, and a
    // text search would answer with the wrong row
    const rowFor = (name: string) => {
      const row = [...document.querySelectorAll('tbody tr')].find((tr) => tr.children[0]?.textContent?.trim() === name);
      if (!row) throw new Error(`no row for ${name}`);
      return row as HTMLElement;
    };
    const editIn = (name: string) =>
      [...rowFor(name).querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Edit') as HTMLButtonElement;

    await waitFor(() => expect(editIn('Dev sandbox').disabled).toBe(false));
    // staging is disabled: S1 still answers `environment.edit` for it — switching an
    // environment out of service must remain reversible — and refuses the three operations that
    // would have to reach the instance
    expect(editIn('Staging').disabled).toBe(false);
    const buttonsIn = (name: string) =>
      [...rowFor(name).querySelectorAll('button')] as HTMLButtonElement[];
    for (const label of ['Test connection', 'Probe capabilities', 'Disable']) {
      const control = buttonsIn('Staging').find((b) => b.textContent?.trim() === label);
      expect(control?.disabled, `${label} on a disabled environment`).toBe(true);
    }
    // production is offered, and the lock it carries is the one S1 answered with — the row's
    // own `status` is ACTIVE or DISABLED and says nothing about either
    expect(editIn('Production').disabled).toBe(false);
    expect(rowFor('Production').textContent).toContain('Locked');
    // one request for a settled page: a gating hook that refetches forever would make the
    // Administration pages unusable, and this is the only place that would show it
    const before = asked;
    await new Promise((r) => setTimeout(r, 400));
    expect(asked - before).toBe(0);
  });

  it('keeps a policy relaxation closed when S1 does not offer it', async () => {
    server.use(
      http.get(`/api/v1/allowed-actions`, () =>
        HttpResponse.json({
          scope: 'platform',
          allowed: [],
          unavailable: [{ action: 'governance.policy.relax', available: false, reasonCode: 'ROLE_GATE_MISSING' }],
        }),
      ),
    );
    renderAt('/governance/policy');
    const relax = (await screen.findByRole('button', { name: /relax/i })) as HTMLButtonElement;
    expect(relax.disabled).toBe(true);
  });
});

/*
 * The environment form on its own (UI-ADM-002 / AC-06). Rendered directly rather than through the
 * page because the page's trigger is gated by allowed-actions, and the rule under test — there is
 * no input anywhere for a secret *value* — belongs to the form.
 */
describe('Environment form (UI-ADM-002 / AC-06)', () => {
  it('offers a Secret reference and never a plaintext credential field', async () => {
    render(
      <AppProviders>
        <MemoryRouter>
          <EnvironmentForm environment={null} onClose={() => undefined} />
        </MemoryRouter>
      </AppProviders>,
    );
    await screen.findByText(/Base URL/i);
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    const save = screen.getByRole('button', { name: /Save environment/ }) as HTMLButtonElement;
    // UI-BR-03: with no Secret reference at all the form cannot be submitted
    expect(save.disabled).toBe(true);
  });
});

/*
 * S5IF-GOV02 / GOV03: registering and correcting a model provider. The rule under test is that
 * an edit never has to re-supply the Key reference, because S1's PATCH leaves absent columns
 * alone — the reason it is a separate operation from registration.
 */

/**
 * The page swaps its Loading tree for the real one, and a node held across that swap is
 * detached: it keeps whatever `disabled` it had at the moment it was taken. Every gate check
 * therefore re-queries, and this returns the live node once S1's answer has enabled it.
 */
function nthDialogLabel(label: RegExp, index = 0): HTMLElement {
  const found = screen.getAllByLabelText(label)[index];
  if (!found) throw new Error(`no element for ${String(label)}`);
  return found;
}

async function enabledTrigger(name: RegExp, index = 0): Promise<HTMLButtonElement> {
  const live = (): HTMLButtonElement => {
    const found = (screen.getAllByRole('button', { name }) as HTMLButtonElement[])[index];
    if (!found) throw new Error(`no button matching ${String(name)}`);
    return found;
  };
  await waitFor(() => expect(live().disabled).toBe(false));
  return live();
}

describe('provider registry (S5IF-GOV02 / GOV03)', () => {
  // The models table shows the same address and reference, so these match on the set.
  it('lists the address and the key reference it was given', async () => {
    renderAt('/governance/providers');
    expect((await screen.findAllByText('https://api.openai.example/v1')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('vault://models/openai/key').length).toBeGreaterThan(0);
  });

  it('registers a provider through the form', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/governance/providers', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ name: captured.body?.name, providerClass: captured.body?.providerClass, endpoint: captured.body?.endpoint, enabled: true }, { status: 201 });
      }),
    );
    renderAt('/governance/providers');
    await userEvent.click(await enabledTrigger(/Register provider/));

    await userEvent.type(screen.getByLabelText(/^Name/), 'inhouse');
    await userEvent.type(screen.getByLabelText(/^Endpoint/), 'https://llm.internal/v1');
    await userEvent.type(screen.getByLabelText(/API key/), 'env://MODEL_API_KEY');

    await userEvent.click(await enabledTrigger(/^Save$/));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({
      name: 'inhouse',
      endpoint: 'https://llm.internal/v1',
      apiKeySecretRef: 'env://MODEL_API_KEY',
      adapter: 'openai_compat',
    });
  });

  // 契约 1.7.1 made the key reference optional because a local inference server has no key to name.
  // This is the shape every `local` catalogue entry arrives in, so the form has to be able to say it:
  // a required box here pushes the operator into inventing `env://` a deployment never set, which
  // registers cleanly and fails the first call.
  it('saves a provider that needs no key at all', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/governance/providers', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ name: captured.body?.name, providerClass: captured.body?.providerClass, endpoint: captured.body?.endpoint, apiKeySecretRef: '', enabled: true }, { status: 201 });
      }),
    );
    renderAt('/governance/providers');
    await userEvent.click(await enabledTrigger(/Register provider/));

    await userEvent.type(screen.getByLabelText(/^Name/), 'ollama');
    await userEvent.type(screen.getByLabelText(/^Endpoint/), 'http://127.0.0.1:11434/v1');

    const save = await enabledTrigger(/^Save$/);
    expect(save.disabled).toBe(false);
    await userEvent.click(save);

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({ name: 'ollama', apiKeySecretRef: '' });
  });

  // An emptied box on an edit is not a removal: PATCH declares `min_length=1` and reads an absent
  // field as "leave it", so the cleared value must not go on the wire.
  it('leaves the key reference off a patch when the box was emptied', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.patch('/api/v1/governance/providers/openai', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ name: 'openai', providerClass: 'APPROVED_EXTERNAL', endpoint: 'https://new.example/v1', apiKeySecretRef: 'vault://models/openai/key', enabled: true });
      }),
    );
    renderAt('/governance/providers');
    await screen.findAllByText('https://api.openai.example/v1');
    await userEvent.click(await enabledTrigger(/Edit provider/));

    const key = screen.getByLabelText(/API key/);
    await userEvent.clear(key);
    const endpoint = screen.getByLabelText(/^Endpoint/);
    await userEvent.clear(endpoint);
    await userEvent.type(endpoint, 'https://new.example/v1');

    const save = await enabledTrigger(/^Save$/);
    expect(save.disabled).toBe(false);
    await userEvent.click(save);

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({ endpoint: 'https://new.example/v1' });
  });

  it('closes the provider editor on cancel', async () => {
    renderAt('/governance/providers');
    await screen.findAllByText('https://api.openai.example/v1');
    await userEvent.click(await enabledTrigger(/Edit provider/));
    expect(nthDialogLabel(/^Name/)).toHaveValue('openai');
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    expect(screen.queryByRole('button', { name: /^Save$/ })).not.toBeInTheDocument();
  });

  it('closes both provider dialogs on cancel without writing anything', async () => {
    let written = 0;
    server.use(
      http.post('/api/v1/governance/providers', () => {
        written += 1;
        return HttpResponse.json({}, { status: 201 });
      }),
      http.post('/api/v1/governance/policy/relaxations', () => {
        written += 1;
        return HttpResponse.json({});
      }),
    );
    renderAt('/governance/providers');
    await userEvent.click(await enabledTrigger(/Register provider/));
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    expect(screen.queryByRole('button', { name: /^Save$/ })).not.toBeInTheDocument();

    // the other dialog the section offers is the edit one, opened from the row it corrects
    await userEvent.click(await enabledTrigger(/Edit provider/));
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    expect(written).toBe(0);
  });

  it('switches a provider off with a body that names nothing else', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.patch('/api/v1/governance/providers/openai', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ name: 'openai', enabled: false, providerClass: 'APPROVED_EXTERNAL', endpoint: 'https://api.openai.example/v1', apiKeySecretRef: 'vault://models/openai/key' });
      }),
    );
    renderAt('/governance/providers');
    await screen.findAllByText('https://api.openai.example/v1');
    await userEvent.click(await enabledTrigger(/Disable provider/));
    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toEqual({ enabled: false });
  });

  it('edits a provider without dropping its key reference', async () => {
    const captured: { body: Record<string, unknown> | null; method: string } = { body: null, method: '' };
    server.use(
      http.patch('/api/v1/governance/providers/openai', async ({ request }) => {
        captured.method = request.method;
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          name: 'openai',
          providerClass: String(captured.body?.providerClass ?? 'PRIVATE_NETWORK'),
          endpoint: String(captured.body?.endpoint ?? ''),
          apiKeySecretRef: 'vault://models/openai/key',
          enabled: Boolean(captured.body?.enabled),
        });
      }),
    );
    renderAt('/governance/providers');
    await screen.findAllByText('https://api.openai.example/v1');
    await userEvent.click(await enabledTrigger(/Edit provider/));

    // the stored reference is already in the field; the operator touches only the address
    expect(screen.getByLabelText(/API key/)).toHaveValue('vault://models/openai/key');
    const endpoint = screen.getByLabelText(/^Endpoint/);
    await userEvent.clear(endpoint);
    await userEvent.type(endpoint, 'https://api.openai.example/v2');

    await userEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.method).toBe('PATCH');
    // Only what moved goes on the wire. S1 reads an absent field as "unchanged" and merges it,
    // while the register path it is avoiding overwrites every column — so sending the stored
    // reference back would be the request that can lose it, not the one that protects it.
    expect(captured.body).toEqual({ endpoint: 'https://api.openai.example/v2' });
  });

  /*
   * 契约 1.17.0: a provider stores which preset it started from and the model names worth
   * offering.  Picking a vendor is the one action that fills both, and the suggested key is
   * offered as a button rather than stamped in — an env:// reference the deployment never set
   * registers cleanly and fails the first call.
   */
  it('starts a new provider from the chosen vendor preset', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/governance/providers', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...captured.body, enabled: true }, { status: 201 });
      }),
    );
    renderAt('/governance/providers');
    await userEvent.click(await enabledTrigger(/Register provider/));

    await userEvent.selectOptions(screen.getByLabelText(/^Vendor/), 'deepseek');
    expect(screen.getByLabelText(/^Endpoint/)).toHaveValue('https://api.deepseek.com/v1');
    expect(screen.getByLabelText(/Suggested model names/)).toHaveValue(
      'deepseek-chat, deepseek-reasoner',
    );

    await userEvent.click(screen.getByRole('button', { name: /Use suggested reference/ }));
    expect(screen.getByLabelText(/API key/)).toHaveValue('env://DEEPSEEK_API_KEY');

    await userEvent.type(screen.getByLabelText(/^Name/), 'deepseek-team');
    await userEvent.click(await enabledTrigger(/^Save$/));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({
      name: 'deepseek-team',
      vendor: 'deepseek',
      endpoint: 'https://api.deepseek.com/v1',
      apiKeySecretRef: 'env://DEEPSEEK_API_KEY',
      suggestedModels: ['deepseek-chat', 'deepseek-reasoner'],
    });
  });

  it('shows the stored vendor and how many model names it suggests', async () => {
    renderAt('/governance/providers');
    expect(await screen.findByText(/2 suggested models/)).toBeInTheDocument();
  });
});

/*
 * S3's own shapes, forwarded whole: `applicableAgents` is a map and `policy.effective` is an
 * object. Rendering either as what the page would rather have crashed the models table and
 * printed `[object Object]` where a policy should read.
 */
/*
 * UI-ADM-003. The form's choices are a subtraction — `ceiling[level]` minus `allowed[level]` —
 * so a fixture that answered the ceiling as the effective policy would leave it offering nothing
 * (or everything illegal) and the tests could not tell the two apart.
 */
describe('requesting a data-policy relaxation (UI-ADM-003)', () => {
  it('offers only what the ceiling still allows, and grants the chosen widening', async () => {
    const captured: { body: Record<string, unknown> | null } = { body: null };
    server.use(
      http.post('/api/v1/governance/policy/relaxations', async ({ request }) => {
        captured.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 'aplax-1', scope: 'organization', operator: 'Ana' }, { status: 201 });
      }),
    );
    renderAt('/governance/policy');
    await userEvent.click(await enabledTrigger(/Request a relaxation/));

    // PUBLIC is the first level with headroom, and the ceiling gives it two more classes than
    // the default policy does — LOCAL_ONLY and PRIVATE_NETWORK are already allowed, so offering
    // them again would be a checkbox that changes nothing
    const level = screen.getByLabelText(/Data classification to widen/) as HTMLSelectElement;
    expect(level.value).toBe('PUBLIC');
    expect(screen.getByLabelText(/^Approved external$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Unrestricted$/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Local only$/)).not.toBeInTheDocument();
    // confirm stays closed until both halves of the request exist
    expect(confirmButton().disabled).toBe(true);

    await userEvent.click(screen.getByLabelText(/^Approved external$/i));
    await userEvent.type(screen.getByLabelText(/Reason/), '外部托管的解析需要，已单独评审');
    await userEvent.click(await enabledTrigger(/^Confirm$/));

    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({
      change: { level: 'PUBLIC', addClasses: ['APPROVED_EXTERNAL'] },
      scope: 'organization',
    });
    // the policy query is invalidated by the grant, so the matrix on screen is re-read
    expect(await screen.findByText('Data classification')).toBeInTheDocument();
  });

  it('says so when a level has nothing left to widen, and never offers PHI_PII a choice', async () => {
    renderAt('/governance/policy');
    await userEvent.click(await enabledTrigger(/Request a relaxation/));
    await userEvent.selectOptions(screen.getByLabelText(/Data classification to widen/), 'PHI_PII');

    expect(await screen.findByText(/may never be sent to a model/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Local only$/)).not.toBeInTheDocument();
    expect(confirmButton().disabled).toBe(true);
  });
});

function confirmButton(): HTMLButtonElement {
  const found = screen.getByRole('button', { name: /^Confirm$/ }) as HTMLButtonElement;
  return found;
}

/*
 * S3's own shapes, read as S3 sends them: `applicableAgents` is a map and `policy.effective` is
 * an object. Rendering either as what the page would rather have had crashed the models table and
 * printed `[object Object]` where a policy should read.
 */
describe('what S3 answers, read as it is answered (S5IF-GOV04 / GOV12)', () => {
  it('lists the agents a model serves by the name the registration form uses', async () => {
    renderAt('/governance/models');
    expect(await screen.findByText('gpt-x-large')).toBeInTheDocument();
    // the raw token is what S3 stores, not what an operator was taught; and the order is the
    // runtime's own — lower priority first, which is the order the models are tried in
    expect(screen.getByText(/Mapping 1; Requirements 2/)).toBeInTheDocument();
  });

  it('shows the classification matrix, including the level nothing may leave for', async () => {
    renderAt('/governance/policy');
    expect(await screen.findByText('Data classification')).toBeInTheDocument();
    expect(screen.getByText('Restricted')).toBeInTheDocument();
    expect(screen.getByText(/Nothing may be sent to a model/)).toBeInTheDocument();
  });
});

/*
 * UI-SET-005 / S5IF-GOV05: registering a model used to be a curl command with a platform action
 * already answering for it in `allowed-actions`. The address and the key reference are not inputs
 * here — S1 joins them from the provider row when it reads the model back.
 *
 * Every value the runtime reads arrives filled. An empty box was never empty on the server: a model
 * named for no agent is tried by nothing, and a context window left out reaches the registry as `0`,
 * which fits no request — so the form posts the same fallbacks the runtime itself assumes, and the
 * tests below pin the body rather than the layout.
 */
const ALL_AGENTS = {
  REQUIREMENTS: 100,
  DOMAIN: 100,
  MAPPING: 100,
  METADATA_DESIGN: 100,
  API: 100,
  UI: 100,
  FIXTURE: 100,
  REVIEW: 100,
};

describe('model registry (S5IF-GOV05 / GOV06)', () => {
  // every test here renders the whole governance page and opens a modal over it; under the
  // coverage instrumentation that pair alone eats most of the default 5 s budget
  const SLOW = 15_000;

  it('registers a model on the provider it belongs to', async () => {
    const captured: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/v1/governance/models', async ({ request }) => {
        captured.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ref: 'openai/new-model', provider: 'openai', model: 'new-model', enabled: true }, { status: 201 });
      }),
    );
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/Register a model/));
    // typed through the DOM: `userEvent.type` pays a full re-render per keystroke, and this file's
    // budget is the render of the page, not the typing of nine characters
    fireEvent.change(await screen.findByLabelText(/Model identifier/i), { target: { value: 'new-model' } });

    // the priority editor is collapsed by default and prefilled: eight agents at the fallback
    // position, which an operator can leave alone or reorder one by one
    await userEvent.click(screen.getByRole('button', { name: /Set each priority/i }));
    const group = screen.getByRole('group', { name: 'Priority' });
    const positions = within(group).getAllByRole('spinbutton');
    expect(positions).toHaveLength(8);
    expect(positions.map((box) => (box as HTMLInputElement).value)).toEqual(positions.map(() => '100'));
    fireEvent.change(positions[7] as HTMLInputElement, { target: { value: '7' } });
    await userEvent.click(await enabledTrigger(/^Save$/));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toMatchObject({ provider: 'openai', model: 'new-model', enabled: true });
    expect(captured[0]).not.toHaveProperty('endpoint');
    expect(captured[0]).not.toHaveProperty('apiKeySecretRef');
    expect(captured[0]).toMatchObject({
      capabilities: { structuredOutput: true, vision: false, maxContextTokens: 8000 },
      applicableAgents: { ...ALL_AGENTS, REVIEW: 7 },
    });
  }, SLOW);

  it('offers the provider row’s saved model names as candidate buttons', async () => {
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/Register a model/));

    // written on the provider row, so the suggestion survives an edited address and is not
    // re-derived from the catalogue's endpoint match
    await userEvent.click(await screen.findByRole('button', { name: 'gpt-4o-mini' }));
    expect(screen.getByLabelText(/^Model identifier/i)).toHaveValue('gpt-4o-mini');
  }, SLOW);

  it('refuses a context window that fits nothing, and says why', async () => {
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/Register a model/));
    fireEvent.change(await screen.findByLabelText(/Model identifier/i), { target: { value: 'tiny-local' } });
    const window = await screen.findByLabelText(/Maximum context/i);
    fireEvent.change(window, { target: { value: '0' } });

    expect(await screen.findByText(/has to be above zero/i)).toBeInTheDocument();
    const save = screen.getByRole('button', { name: /^Save$/ }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  }, SLOW);

  it('drops the agent the operator unnames', async () => {
    const captured: Record<string, unknown>[] = [];
    server.use(
      http.post('/api/v1/governance/models', async ({ request }) => {
        captured.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ ref: 'openai/new-model', provider: 'openai', model: 'new-model', enabled: true }, { status: 201 });
      }),
    );
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/Register a model/));
    fireEvent.change(await screen.findByLabelText(/Model identifier/i), { target: { value: 'mapping-only' } });
    // looked up inside the group and matched by its label text: a page-wide `getByRole` by name
    // computes an accessible name for every checkbox on the screen, which costs more than the
    // behaviour it is here to check
    const group = screen.getByRole('group', { name: 'Applicable agents' });
    const review = within(group).getAllByRole('checkbox').find((box) => box.closest('label')?.textContent?.includes('Review'));
    if (!review) throw new Error('the review agent is not offered');
    fireEvent.click(review);
    await userEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]?.applicableAgents).toEqual({
      REQUIREMENTS: 100, DOMAIN: 100, MAPPING: 100, METADATA_DESIGN: 100, API: 100, UI: 100, FIXTURE: 100,
    });
  }, SLOW);

  it('states what a box decides, and warns when no agent is named', async () => {
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/Register a model/));
    await screen.findByLabelText(/Model identifier/i);

    // one question mark per field the runtime reads, and each opens its own consequence. The
    // priority ordering is counted even with its panel closed, because the fallback number goes out
    // to S1 whether or not the panel is ever opened.
    const tips = screen.getAllByRole('button', { name: 'Why?' });
    expect(tips.length).toBe(9);
    await userEvent.click(tips[4] as HTMLElement);
    expect(await screen.findByText(/A cutoff, not a length/i)).toBeInTheDocument();

    const group = screen.getByRole('group', { name: 'Applicable agents' });
    for (const box of within(group).getAllByRole('checkbox')) fireEvent.click(box);

    // naming no agent is legal and useless; the page states the second half and leaves the first to
    // S1, which is the only thing that knows whether this operator may write the row at all
    expect(await screen.findByText(/No agent named/i)).toBeInTheDocument();
    expect(within(group).getAllByRole('checkbox', { checked: false })).toHaveLength(8);

    // and one tap puts them all back at the fallback position
    await userEvent.click(await enabledTrigger(/^All agents$/));
    expect(screen.queryByText(/No agent named/i)).not.toBeInTheDocument();
    expect(within(group).getAllByRole('checkbox', { checked: true })).toHaveLength(8);
  }, SLOW);

  it('disables one model by its reference and names nothing else', async () => {
    const seen: { ref: string | null; body: Record<string, unknown> | null } = { ref: null, body: null };
    server.use(
      http.patch('/api/v1/governance/models/*', async ({ request }) => {
        // the reference is `provider/model`, which is why S1 declares the path as `{ref:path}`; the
        // client percent-encodes the slash and the server decodes it, so read the tail of the URL
        const path = new URL(request.url).pathname;
        seen.ref = decodeURIComponent(path.slice('/api/v1/governance/models/'.length));
        seen.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ref: seen.ref, enabled: false });
      }),
    );
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/Disable model/));
    await waitFor(() => expect(seen.body).not.toBeNull());
    expect(seen.body).toEqual({ enabled: false });
    // S3's reference carries the slash, so the path has to arrive whole
    expect(seen.ref).toBe('openai/gpt-x-large');
  });

  it('re-reads the list after the row is corrected', async () => {
    let reads = 0;
    server.use(
      http.get('/api/v1/governance/models', () => {
        reads += 1;
        return HttpResponse.json(db.models);
      }),
      http.patch('/api/v1/governance/models/*', () => HttpResponse.json({ ...db.models.items[0], unitPriceOutputPer1k: 0.05 })),
    );
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/^Edit model$/));
    const out = screen.getByLabelText(/Price per 1k output/i) as HTMLInputElement;
    await userEvent.clear(out);
    await userEvent.type(out, '0.05');
    const before = reads;
    await userEvent.click(await enabledTrigger(/^Save$/));
    // the table is the proof the write landed; a form that closes without a re-read leaves the
    // operator staring at the number they came here to change
    await waitFor(() => expect(reads).toBeGreaterThan(before));
  }, SLOW);
});

/*
 * S5IF-GOV06 as an edit rather than a re-registration. The row arrives with its own values, the two
 * halves of its reference cannot be moved from here, and the body names only what the operator
 * touched — because S3's register path overwrites every column a body omits, an edit that resent the
 * row minus the model's own `endpoint` would quietly move it back onto the provider's.
 */
describe('editing a registered model (S5IF-GOV06)', () => {
  const SLOW = 15_000;
  function capturePatch() {
    const seen: { ref: string | null; body: Record<string, unknown> | null } = { ref: null, body: null };
    server.use(
      http.patch('/api/v1/governance/models/*', async ({ request }) => {
        const path = new URL(request.url).pathname;
        seen.ref = decodeURIComponent(path.slice('/api/v1/governance/models/'.length));
        seen.body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ref: seen.ref, ...seen.body });
      }),
    );
    return seen;
  }

  it('opens the first row with its own numbers in the boxes', async () => {
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/^Edit model$/));

    expect((screen.getByLabelText(/^Model identifier/i) as HTMLInputElement).value).toBe('gpt-x-large');
    expect((screen.getByLabelText(/Maximum context/i) as HTMLInputElement).value).toBe('128000');
    expect((screen.getByLabelText(/Price per 1k input/i) as HTMLInputElement).value).toBe('0.01');
    // the registration default is eight agents at 100; this row names two, at their own positions
    const group = screen.getByRole('group', { name: 'Applicable agents' });
    expect(within(group).getAllByRole('checkbox', { checked: true })).toHaveLength(2);
    expect(within(group).getAllByRole('checkbox', { checked: false })).toHaveLength(6);
  });

  it('holds provider and model identifier still, because they are the row’s address', async () => {
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/^Edit model$/));

    expect(screen.getByLabelText(/^Provider/i)).toBeDisabled();
    expect(screen.getByLabelText(/^Model identifier/i)).toBeDisabled();
  });

  it('sends only the field that moved — and never the address or the key', async () => {
    const seen = capturePatch();
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/^Edit model$/));

    const out = screen.getByLabelText(/Price per 1k output/i) as HTMLInputElement;
    await userEvent.clear(out);
    await userEvent.type(out, '0.05');
    await userEvent.click(await enabledTrigger(/^Save$/));

    await waitFor(() => expect(seen.body).not.toBeNull());
    expect(seen.body).toEqual({ unitPriceOutputPer1k: 0.05 });
    expect(JSON.stringify(seen.body)).not.toMatch(/endpoint|apiKeySecretRef|enabled|applicableAgents/);
    expect(seen.ref).toBe('openai/gpt-x-large');
  }, SLOW);

  it('keeps Save down while the form still says what the row already says', async () => {
    renderAt('/governance/models');
    await userEvent.click(await enabledTrigger(/^Edit model$/));

    // re-typing the stored number is not a change: a body of `{}` would be refused by S1 as
    // "nothing named", and offering Save for a request the server will reject is a dead end with a
    // button on it
    const context = screen.getByLabelText(/Maximum context/i) as HTMLInputElement;
    await userEvent.clear(context);
    await userEvent.type(context, '128000');
    expect((screen.getByRole('button', { name: /^Save$/ }) as HTMLButtonElement).disabled).toBe(true);

    await userEvent.clear(context);
    await userEvent.type(context, '120000');
    await waitFor(() => expect((screen.getByRole('button', { name: /^Save$/ }) as HTMLButtonElement).disabled).toBe(false));
  }, SLOW);
});
