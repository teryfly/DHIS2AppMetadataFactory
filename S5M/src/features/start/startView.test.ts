/*
 * UM-SET-001 … 006 as amended: the guide is one step per screen, and the step that needs a
 * registration opens that registration here instead of sending the operator to the page that holds
 * the form. Two things are therefore worth pinning down, and neither is about the forms themselves
 * (they have their own specs): which step the guide opens on, and that opening a step never leaves
 * `/start`.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { i18n } from '@/i18n';
import StartView from './StartView.vue';
import * as queries from '@/api/queries';

const pushed = vi.hoisted(() => ({ calls: [] as string[] }));

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: (to: string) => pushed.calls.push(to) }),
  useRoute: () => ({ path: '/start' }),
}));

// The guide only ever needs two reads: the facts of GET /setup and the platform's allowed-actions.
// Both are replaced by refs the tests can move, so a step is seen to pass without a second mount.
vi.mock('@/api/queries', async () => {
  const { ref } = await import('vue');
  const setup = ref<Record<string, unknown>>({});
  const platform = ref<Record<string, unknown>>({ allowed: [], unavailable: [] });
  const refetches = { setup: 0 };
  return {
    useSetup: () => ({ data: setup, isLoading: ref(false), isError: ref(false), refetch: () => (refetches.setup += 1) }),
    usePlatformActions: () => ({ data: platform, isLoading: ref(false), isError: ref(false) }),
    __guide: { setup, platform, refetches },
  };
});

vi.mock('@/fui', () => ({
  FuiAsync: { props: ['loading', 'error'], template: '<div><slot /></div>' },
  FuiNotice: { props: ['tone'], template: '<div class="stub-notice"><slot /></div>' },
  FuiCard: {
    props: ['title', 'subtitle'],
    template: '<section class="stub-card"><h3>{{ title }}</h3><p class="stub-sub">{{ subtitle }}</p><slot /></section>',
  },
  FuiButton: {
    props: ['label', 'variant', 'block', 'disabled', 'size'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

// The guide's own buttons are gated through FuiActionButton, which reads the operator store and the
// network state. Its gating is its own tested behaviour; here it is stubbed to a plain button that
// still reports which action it was asked to gate, because "the environment step opens the form" and
// "the button that opens it is gated by environment.create" are both claims this view makes.
vi.mock('@/fui/FuiActionButton.vue', () => ({
  default: {
    props: ['action', 'label', 'variant', 'block', 'loading', 'disabled', 'platform', 'size'],
    emits: ['click', 'blocked'],
    template: '<button :disabled="disabled" :data-action="action" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

// imported by path inside the sheets, and the barrel mock above never reaches them
vi.mock('@/features/governance/ProviderSheet.vue', () => ({
  default: { props: ['modelValue', 'provider'], emits: ['update:modelValue', 'saved'], template: '<div class="stub-provider-sheet" @click="$emit(\'saved\')">provider</div>' },
}));
vi.mock('@/features/governance/ModelSheet.vue', () => ({
  default: { props: ['modelValue'], emits: ['update:modelValue', 'saved'], template: '<div class="stub-model-sheet">model</div>' },
}));
vi.mock('@/features/environments/EnvironmentFormSheet.vue', () => ({
  default: { props: ['modelValue', 'environment'], emits: ['update:modelValue', 'saved'], template: '<div class="stub-environment-sheet">environment</div>' },
}));

const guide = (queries as unknown as { __guide: { setup: { value: Record<string, unknown> }; platform: { value: Record<string, unknown> }; refetches: { setup: number } } }).__guide;

/** A deployment where a stand-in vendor is still registered: step 1 is the work in front of them. */
function bareFacts(): Record<string, unknown> {
  return {
    configuration: { problems: [] },
    identity: { pendingPasswordChange: false },
    providers: { 'openai-compatible': 'fake' },
    environments: { total: 0, inService: 0, neverTested: [], nonProductionInService: 0 },
    intelligence: { providers: 0, models: 0, modelsEnabled: 0 },
    projects: { total: 0, active: 0 },
    credentials: null,
  };
}

/** Wiring fixed, no model yet: the guide walks on to the model step by itself. */
function wired(overrides: Record<string, unknown>): Record<string, unknown> {
  return { ...bareFacts(), providers: { 'openai-compatible': 'real' }, ...overrides };
}

function mounted() {
  return mount(StartView, { global: { plugins: [i18n] } });
}

function chips(wrapper: ReturnType<typeof mounted>) {
  return wrapper.findAll('.start__chip');
}

/** The current step's card is the first of the two panels; its buttons are in the guide's own order. */
function card(wrapper: ReturnType<typeof mounted>) {
  return wrapper.findAll('.stub-card')[0];
}

function cardTitle(wrapper: ReturnType<typeof mounted>): string {
  return card(wrapper).find('h3').text();
}

/** the deployment's capability ports, reported at the foot of the page rather than as a step */
function capabilityCard(wrapper: ReturnType<typeof mounted>) {
  const found = wrapper.findAll('.stub-card').find((c) => /Capabilities|能力接入/.test(c.find('h3').text()));
  if (!found) throw new Error(`no card on the page names the capability ports: ${wrapper.text()}`);
  return found;
}

function buttons(wrapper: ReturnType<typeof mounted>) {
  const found = card(wrapper).findAll('button');
  if (!found.length) throw new Error(`the current step card has no button: ${card(wrapper).text()}`);
  return found;
}

beforeEach(() => {
  i18n.global.locale.value = 'en';
  pushed.calls.length = 0;
  guide.refetches.setup = 0;
  guide.setup.value = bareFacts();
  guide.platform.value = {
    allowed: [{ action: 'project.create', available: true, targetId: null, reasonCode: null, details: {} }],
    unavailable: [],
  };
});

describe('StartView one step per screen (UM-SET-006)', () => {
  it('opens on the first step that is not ready and marks it in the rail', async () => {
    const wrapper = mounted();
    await flushPromises();
    const rail = chips(wrapper);
    // four steps: the capability wiring is a fact the page reports, not a task it assigns
    expect(rail).toHaveLength(4);
    expect(rail[0].attributes('aria-current')).toBe('step');
    expect(rail[1].attributes('aria-current')).toBeUndefined();
    expect(cardTitle(wrapper)).toBe('Model providers and models');
  });

  it('re-renders the capability names when the language changes, without a remount', async () => {
    // The step list is a computed, and the server's port labels are mapped through `labels.ts`.
    // Resolve those once at module load and a Chinese operator would read "Requirement parsing" in
    // the middle of a Chinese sentence, so the mapping has to follow the locale live.
    guide.setup.value = { ...bareFacts(), providers: { S3: 'fake' } };
    const wrapper = mounted();
    await flushPromises();
    expect(capabilityCard(wrapper).text()).toContain('Requirement parsing');
    i18n.global.locale.value = 'zh-CN';
    await nextTick();
    expect(capabilityCard(wrapper).find('h3').text()).toBe('能力接入');
    expect(capabilityCard(wrapper).text()).toContain('需求解析');
    i18n.global.locale.value = 'en';
  });

  it('a withheld section is pending, never a pass read off a zero', async () => {
    guide.setup.value = { ...bareFacts(), intelligence: null };
    guide.platform.value = { allowed: [], unavailable: [{ action: 'project.create', available: false, targetId: null, reasonCode: 'PROJECT_ARCHIVED', details: {} }] };
    const wrapper = mounted();
    await flushPromises();
    const states = chips(wrapper).map((chip) => chip.find('.start__state').text());
    // models: the governance counts are not visible to this identity; project: the action was
    // refused. Neither may render as "Not yet", which would tell the operator to go and do it.
    expect(states[0]).toBe('Partial');
    expect(states[2]).toBe('Partial');
  });

  it('states the capability wiring as a fact, with nothing to click', async () => {
    // `GET /setup.providers` is whether the three capability ports are real or stand-ins — a
    // deployment choice made in the server's own env file. It used to be step one of the checklist,
    // which asked the operator to complete something no browser control can change.
    guide.setup.value = { ...bareFacts(), providers: { S3: 'fake' } };
    const wrapper = mounted();
    await flushPromises();
    expect(capabilityCard(wrapper).text()).toContain('Requirement parsing');
    expect(capabilityCard(wrapper).findAll('button')).toHaveLength(0);
    expect(pushed.calls).toEqual([]);
  });

  it('the model step opens both registrations it depends on, without leaving /start', async () => {
    guide.setup.value = wired({ environments: { total: 1, inService: 1, neverTested: [], nonProductionInService: 1 } });
    const wrapper = mounted();
    await flushPromises();
    // the model step is the first one now: nothing else on the deployment works without a model
    expect(cardTitle(wrapper)).toBe('Model providers and models');
    const [model, provider] = buttons(wrapper);
    expect(model.text()).toBe('Register a model');
    expect(model.attributes('data-action')).toBe('governance.model.register');
    expect(provider.text()).toBe('Register a provider');
    expect(provider.attributes('data-action')).toBe('governance.provider.register');
    await provider.trigger('click');
    expect(wrapper.find('.stub-provider-sheet').exists()).toBe(true);
    expect(wrapper.find('.stub-model-sheet').exists()).toBe(false);
    expect(pushed.calls).toEqual([]);
    await chips(wrapper)[0].trigger('click'); // selecting the step again closes the sheet
    await buttons(wrapper)[0].trigger('click');
    expect(wrapper.find('.stub-model-sheet').exists()).toBe(true);
  });

  it('the environment step opens the environment form, not the environments page', async () => {
    guide.setup.value = {
      ...bareFacts(),
      providers: { 'openai-compatible': 'real' },
      environments: { total: 1, inService: 1, neverTested: ['env-a'], nonProductionInService: 1 },
      intelligence: { providers: 1, models: 2, modelsEnabled: 1 },
    };
    const wrapper = mounted();
    await flushPromises();
    expect(cardTitle(wrapper)).toBe('DHIS2 environments');
    // gated by the server's answer, not by "the form would probably work"
    expect(buttons(wrapper)[0].attributes('data-action')).toBe('environment.create');
    await buttons(wrapper)[0].trigger('click');
    expect(wrapper.find('.stub-environment-sheet').exists()).toBe(true);
    expect(pushed.calls).toEqual([]);
  });

  it('a step that is not a form on this page still sends the operator where the work is', async () => {
    guide.setup.value = wired({
      environments: { total: 1, inService: 1, neverTested: [], nonProductionInService: 1 },
      intelligence: { providers: 1, models: 2, modelsEnabled: 1 },
    });
    const wrapper = mounted();
    await flushPromises();
    // every registration is done; what is left is the project itself
    expect(cardTitle(wrapper)).toBe('First project');
    await buttons(wrapper)[0].trigger('click');
    expect(pushed.calls).toEqual(['/projects/new']);
  });

  it('closing a registration re-reads the facts and lands on whatever is now next', async () => {
    guide.setup.value = wired({ environments: { total: 1, inService: 1, neverTested: [], nonProductionInService: 1 } });
    const wrapper = mounted();
    await flushPromises();
    await buttons(wrapper)[1].trigger('click');
    expect(wrapper.find('.stub-provider-sheet').exists()).toBe(true);
    await wrapper.find('.stub-provider-sheet').trigger('click'); // the sheet emits `saved`
    await flushPromises();
    expect(wrapper.find('.stub-provider-sheet').exists()).toBe(false);
    expect(guide.refetches.setup).toBeGreaterThan(0);
    expect(wrapper.find('.stub-model-sheet').exists()).toBe(false);
  });

  it('a chip selects its own step rather than the first unfinished one', async () => {
    const wrapper = mounted();
    await flushPromises();
    await chips(wrapper)[2].trigger('click');
    expect(cardTitle(wrapper)).toBe('First project');
    expect(pushed.calls).toEqual([]);
  });
});
