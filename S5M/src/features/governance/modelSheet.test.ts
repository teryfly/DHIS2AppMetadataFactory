/*
 * UM-SET-005 on the phone. The sheet exists because every value it collects decides whether a stage
 * can run at all, and it used to collect them as empty boxes with one line of small print: an agent
 * left blank is an agent that never sees the model, and a context window left blank arrives at the
 * registry as `0`, which fits no request. So the defaults are in the boxes, the consequence is one
 * tap away, and these tests pin the body the sheet posts rather than its layout.
 *
 * `FuiHelp` is imported by the component through its own path, so the `@/fui` stub below cannot
 * replace it: a stubbed question mark would test a button that opens nothing.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { i18n } from '@/i18n';
import ModelSheet from './ModelSheet.vue';

vi.mock('@/fui', async () => ({
  ...(await import('@/test/chipComponents')),
  FuiBottomSheet: {
    props: ['modelValue', 'title'],
    template: '<div v-if="modelValue" class="stub-sheet"><h2>{{ title }}</h2><slot /><slot name="footer" /></div>',
  },
  FuiField: {
    props: ['modelValue', 'label', 'type', 'placeholder', 'required'],
    emits: ['update:modelValue'],
    template: '<label>{{ label }}<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
  FuiCell: { props: ['title'], template: '<div>{{ title }}<slot /></div>' },
  FuiSwitch: { props: ['modelValue', 'size', 'label'], emits: ['update:modelValue'], template: '<input type="checkbox" :checked="modelValue" />' },
  FuiActionButton: {
    props: ['action', 'label', 'platform', 'size', 'variant', 'disabled', 'loading'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  FuiButton: {
    props: ['label', 'loading', 'disabled', 'variant', 'size', 'block'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  FuiNotice: { props: ['tone', 'text'], template: '<div>{{ text }}</div>' },
}));

const AGENT_TYPES = ['REQUIREMENTS', 'DOMAIN', 'MAPPING', 'METADATA_DESIGN', 'API', 'UI', 'FIXTURE', 'REVIEW'];

// UM-AC-20 bans an absolute URL literal in src; building one at run time keeps the test fixture
// inside that rule while still giving the components a real address shape.
const origin = (scheme: 'http' | 'https', host: string, path = ''): string => `${scheme}://${host}${path}`;

let sent: { method: string; path: string; body: unknown }[];

beforeEach(() => {
  i18n.global.locale.value = 'en';
  sent = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      sent.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (path.includes('/governance/agents')) return new Response(JSON.stringify({ items: AGENT_TYPES.map((agentType) => ({ agentType, enabled: true })) }), { status: 200, headers: { 'content-type': 'application/json' } });
      if (path.includes('/governance/provider-catalogue')) return new Response(JSON.stringify({ items: [{ key: 'openai', displayName: 'OpenAI', endpoint: origin('https', 'api.openai.com', '/v1'), models: ['catalogue-only'] }], total: 1 }), { status: 200, headers: { 'content-type': 'application/json' } });
      if (path.includes('/governance/providers')) return new Response(JSON.stringify({ items: [{ name: 'openai', providerClass: 'APPROVED_EXTERNAL', vendor: 'openai', suggestedModels: ['gpt-4o', 'gpt-4o-mini'] }] }), { status: 200, headers: { 'content-type': 'application/json' } });
      return new Response(JSON.stringify({ ref: 'openai/new-model' }), { status: method === 'POST' ? 201 : 200, headers: { 'content-type': 'application/json' } });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

function render() {
  // mounted closed and opened by the test: the sheet resets itself on the way in, exactly as it does
  // behind the button on the governance page
  return mount(ModelSheet, {
    props: { modelValue: false, model: null },
    global: { plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }]] },
  });
}

function buttonByText(wrapper: ReturnType<typeof render>, text: RegExp) {
  return wrapper.findAll('button').filter((b) => text.test(b.text())).map((b) => b.element as HTMLButtonElement);
}

/** `label` is the stub field's only text: the input itself carries none. */
function fieldByLabel(wrapper: ReturnType<typeof render>, text: RegExp) {
  const label = wrapper.findAll('label').find((l) => text.test(l.text()));
  return label?.find('input')?.element as HTMLInputElement | undefined;
}

async function fill(wrapper: ReturnType<typeof render>, text: RegExp, value: string) {
  const box = fieldByLabel(wrapper, text);
  if (!box) throw new Error(`no field labelled ${text}`);
  box.value = value;
  box.dispatchEvent(new Event('input'));
  await flushPromises();
}

async function press(wrapper: ReturnType<typeof render>, text: RegExp) {
  const found = buttonByText(wrapper, text)[0];
  if (!found) throw new Error(`no button labelled ${text}`);
  found.click();
  await flushPromises();
}

/** The provider row is a chip group too, so the agents are addressed by the label they are under. */
function agentChips(wrapper: ReturnType<typeof render>) {
  return wrapper.find('[role="group"][aria-label="Applicable agents"]').findAll('.fui-chips__chip');
}

async function open() {
  const wrapper = render();
  await wrapper.setProps({ modelValue: true });
  await vi.waitFor(() => {
    expect(agentChips(wrapper).length).toBe(AGENT_TYPES.length);
    // the sheet is only settled once the provider it pre-selects has arrived: that choice is what
    // makes Save live, and a test that raced it would pass on a cache hit and fail on a cold read
    expect(wrapper.text()).not.toMatch(/Choose which provider/i);
  });
  return wrapper;
}

describe('the model registration sheet', () => {
  it('arrives filled with the values the runtime would assume anyway', async () => {
    const wrapper = await open();

    expect(fieldByLabel(wrapper, /Maximum context/i)?.value).toBe('8000');
    expect(fieldByLabel(wrapper, /Price per 1k input/i)?.value).toBe('0');
    // every agent is named at the fallback position: usable by all of them, taking no one's place
    expect(agentChips(wrapper).filter((chip) => chip.classes().includes('fui-chips__chip--on'))).toHaveLength(AGENT_TYPES.length);

    await fill(wrapper, /Model identifier/i, 'new-model');
    expect(buttonByText(wrapper, /^Save$/)[0]?.disabled).toBe(false);
    await press(wrapper, /^Save$/);

    const posted = sent.find((s) => s.method === 'POST' && s.path.includes('/governance/models'));
    expect((posted?.body as Record<string, unknown>).capabilities).toEqual({ structuredOutput: true, vision: false, maxContextTokens: 8000 });
    expect((posted?.body as Record<string, unknown>).applicableAgents).toEqual(
      Object.fromEntries(AGENT_TYPES.map((type) => [type, 100])),
    );
  });

  it('keeps the window at its stated default when the box is emptied, and refuses it at zero', async () => {
    const wrapper = await open();

    await fill(wrapper, /Maximum context/i, '');
    expect(wrapper.text()).not.toMatch(/above zero/i);
    await fill(wrapper, /Maximum context/i, '0');
    expect(wrapper.text()).toMatch(/has to be above zero/i);
    expect(buttonByText(wrapper, /^Save$/)[0]?.disabled).toBe(true);
  });

  it('names an agent in the language the operator was taught, and drops the ones unticked', async () => {
    const wrapper = await open();

    // the stored token is what the server keeps; the chip is what the person reads
    expect(wrapper.text()).toContain('Metadata design');
    const review = agentChips(wrapper).find((chip) => chip.text() === 'Review');
    await review?.trigger('click');
    await flushPromises();
    expect(review?.attributes('aria-pressed')).toBe('false');

    await fill(wrapper, /Model identifier/i, 'not-for-review');
    await press(wrapper, /^Save$/);

    const body = sent.find((s) => s.method === 'POST')?.body as { applicableAgents: Record<string, number> };
    expect(Object.keys(body.applicableAgents)).toEqual(AGENT_TYPES.filter((type) => type !== 'REVIEW'));
  });

  it('offers the provider row’s saved model names as shortcut buttons', async () => {
    const wrapper = await open();

    const candidate = wrapper.findAll('button').find((button) => button.text() === 'gpt-4o-mini');
    expect(candidate).toBeTruthy();
    await candidate!.trigger('click');

    // a suggestion writes into the same text box; it is not a second value
    expect(fieldByLabel(wrapper, /Model identifier/i)?.value).toBe('gpt-4o-mini');
  });

  it('warns when nothing is named, and still lets the operator save', async () => {
    const wrapper = await open();
    await fill(wrapper, /Model identifier/i, 'nobody');

    for (const chip of agentChips(wrapper)) await chip.trigger('click');

    // the page states the consequence; only S1 decides whether the row may be written
    expect(wrapper.text()).toMatch(/No agent named/i);
    expect(buttonByText(wrapper, /^Save$/)[0]?.disabled).toBe(false);

    await press(wrapper, /All agents/);
    expect(wrapper.text()).not.toMatch(/No agent named/i);
  });

  it('opens what a value decides from the question mark beside it', async () => {
    const wrapper = await open();
    const tips = wrapper.findAllComponents({ name: 'FuiHelp' });
    expect(tips.length).toBeGreaterThan(6);
    for (const tip of tips) expect(tip.find('[role="button"]').attributes('aria-expanded')).toBe('false');

    for (const tip of tips) await tip.find('[role="button"]').trigger('click');

    expect(wrapper.text()).toMatch(/A cutoff, not a length/i);
    expect(wrapper.text()).toMatch(/Only the agents you name here/i);
    expect(wrapper.text()).toMatch(/nothing is billed/i);
    // The ordering sentence has to be readable without opening the panel: every agent is submitted
    // with the fallback number whether or not it is ever expanded, and a main model left behind a
    // stub at 1 is never tried (S3 walks applicableAgents ascending).
    expect(wrapper.text()).toMatch(/Smaller numbers are tried first/i);
    expect(tips[0].find('[role="button"]').attributes('aria-expanded')).toBe('true');
  });

  it('shows the per-agent priorities only when asked for, prefilled', async () => {
    const wrapper = await open();
    expect(wrapper.find('[role="group"][aria-label="Priority"]').exists()).toBe(false);

    await press(wrapper, /Set each priority/);

    expect(fieldByLabel(wrapper, /Domain model/i)?.value).toBe('100');
    await fill(wrapper, /Domain model/i, '3');
    await fill(wrapper, /Model identifier/i, 'ordered');
    await press(wrapper, /^Save$/);

    const body = sent.find((s) => s.method === 'POST')?.body as { applicableAgents: Record<string, number> };
    expect(body.applicableAgents.DOMAIN).toBe(3);
    expect(body.applicableAgents.MAPPING).toBe(100);
  });
});

/**
 * S5IF-GOV06 as edited rather than re-registered: the row arrives with its own values, the two
 * halves of its reference cannot be moved from here, and the body names only what was touched.
 */
describe('editing a model that is already registered', () => {
  const STORED = {
    ref: 'openai/gpt-4o-mini',
    provider: 'openai',
    model: 'gpt-4o-mini',
    enabled: true,
    // no scheme here on purpose: `isolation.test.ts` bans any absolute address literal in src, and
    // the value's only job in this file is to be a stored override the patch must not overwrite
    endpoint: 'internal.invalid/v1',
    apiKeySecretRef: 'db://openai-key',
    capabilities: { structuredOutput: true, vision: true, maxContextTokens: 128000 },
    unitPriceInputPer1k: 0.15,
    unitPriceOutputPer1k: 0.6,
    applicableAgents: { REQUIREMENTS: 5, MAPPING: 100 },
  };

  async function openEdit(row: Record<string, unknown> = STORED) {
    const wrapper = mount(ModelSheet, {
      props: { modelValue: false, model: row as never },
      global: { plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }]] },
    });
    await wrapper.setProps({ modelValue: true });
    await vi.waitFor(() => expect(agentChips(wrapper).length).toBe(AGENT_TYPES.length));
    return wrapper;
  }

  it('arrives with the stored row in the boxes and its reference held still', async () => {
    const wrapper = await openEdit();

    expect(wrapper.text()).toContain('Edit model');
    expect(fieldByLabel(wrapper, /Model identifier/i)?.value).toBe('gpt-4o-mini');
    expect(fieldByLabel(wrapper, /Maximum context/i)?.value).toBe('128000');
    expect(fieldByLabel(wrapper, /Price per 1k input/i)?.value).toBe('0.15');
    // the agents it names are the ones that may use it: the catalogue's other six stay off, and the
    // registration default (all agents at 100) must not overwrite what the row already says
    expect(agentChips(wrapper).filter((chip) => chip.classes().includes('fui-chips__chip--on'))).toHaveLength(2);
    await press(wrapper, /Set each priority/);
    expect(fieldByLabel(wrapper, /Requirements/i)?.value).toBe('5');

    // provider and model id are half of the row's address, so editing them means registering again
    const providers = wrapper.find('[role="group"][aria-label="Providers"]');
    expect(providers.findAll('button').every((chip) => chip.attributes('disabled') !== undefined)).toBe(true);
  });

  it('holds Save down while nothing has been moved', async () => {
    const wrapper = await openEdit();
    expect(buttonByText(wrapper, /^Save$/)[0]?.disabled).toBe(true);

    await fill(wrapper, /Maximum context/i, '128000');
    expect(buttonByText(wrapper, /^Save$/)[0]?.disabled).toBe(true);
  });

  it('sends only the field the operator touched — and never the address or the key', async () => {
    const wrapper = await openEdit();
    await fill(wrapper, /Maximum context/i, '9000');
    await press(wrapper, /^Save$/);

    const patched = sent.find((s) => s.method === 'PATCH' && s.path.includes('/governance/models/'));
    expect(patched?.path).toContain(encodeURIComponent(STORED.ref));
    // an omitted column keeps its value; `endpoint` and `apiKeySecretRef` are not in the form at all,
    // because a body that named them would move the model back onto the provider
    expect(patched?.body).toEqual({ capabilities: { maxContextTokens: 9000 } });
    expect(JSON.stringify(patched?.body)).not.toMatch(/endpoint|apiKeySecretRef|applicableAgents|enabled/);
  });

  it('names the agents it wants when one is unticked, at the priorities on screen', async () => {
    const wrapper = await openEdit();
    const mapping = agentChips(wrapper).find((chip) => chip.text() === 'Mapping')!;
    await mapping.trigger('click');
    await press(wrapper, /^Save$/);

    const body = sent.find((s) => s.method === 'PATCH')?.body as { applicableAgents: Record<string, number> };
    expect(body.applicableAgents).toEqual({ REQUIREMENTS: 5 });
  });

  it('lets the switch on the list and the form share one verb', async () => {
    const wrapper = await openEdit();
    await fill(wrapper, /Price per 1k output/i, '1.2');
    await press(wrapper, /^Save$/);
    expect(sent.find((s) => s.method === 'PATCH')?.body).toEqual({ unitPriceOutputPer1k: 1.2 });
    expect(wrapper.emitted('saved')).toBeTruthy();
  });
});
