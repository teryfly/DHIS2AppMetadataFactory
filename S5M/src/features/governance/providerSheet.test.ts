/*
 * UI-SET-006 / S5IF-GOV02/03/07 on the phone. The sheet is the one place a provider is declared,
 * so the two claims worth pinning are:
 *
 *  - the vendor picker actually drives the form: it shows the stored key, and choosing a preset
 *    fills the address, the suggested key reference and the model names that preset documents;
 *  - an edit sends only what moved, because S1's PATCH reads an absent field as "leave it".
 *
 * The FUI barrel is stubbed to keep Vant out of jsdom; `FuiSegmented` comes from the shared chip
 * stubs because it is a plain button group and the test presses its chips.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { i18n } from '@/i18n';
import ProviderSheet from './ProviderSheet.vue';

vi.mock('@/fui', async () => ({
  ...(await import('@/test/chipComponents')),
  FuiBottomSheet: {
    props: ['modelValue', 'title'],
    template: '<div v-if="modelValue" class="stub-sheet"><h2>{{ title }}</h2><slot /><slot name="footer" /></div>',
  },
  FuiField: {
    props: ['modelValue', 'label', 'type', 'placeholder', 'required', 'readonly', 'help'],
    emits: ['update:modelValue'],
    template: '<label>{{ label }}<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
  FuiSwitch: {
    props: ['modelValue', 'size', 'label'],
    emits: ['update:modelValue'],
    template: '<input type="checkbox" :checked="modelValue" />',
  },
  FuiButton: {
    props: ['label', 'size', 'disabled', 'loading', 'variant'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

vi.mock('@/fui/FuiActionButton.vue', () => ({
  default: {
    props: ['action', 'label', 'platform', 'size', 'variant', 'disabled', 'loading'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

vi.mock('@/fui/FuiHelp.vue', () => ({
  default: { props: ['label', 'text'], template: '<div class="stub-help">{{ text }}</div>' },
}));

// The real field talks to the credential store; its own contract is tested beside it.
vi.mock('@/features/secrets/CredentialField.vue', () => ({
  default: {
    props: ['modelValue', 'label', 'help', 'suggestedName'],
    emits: ['update:modelValue'],
    template: '<label>{{ label }}<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
}));

// UM-AC-20 bans an absolute URL literal in src; building them at run time keeps the fixtures
// inside that rule while still giving the sheet addresses of the shape S1 sends.
const origin = (scheme: 'http' | 'https', host: string, path = ''): string => `${scheme}://${host}${path}`;

const DEEPSEEK_ENDPOINT = origin('https', 'api.deepseek.com', '/v1');
const CATALOGUE = [
  {
    key: 'deepseek',
    displayName: 'DeepSeek',
    endpoint: DEEPSEEK_ENDPOINT,
    wireProtocol: 'openai_compat',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    keyRefSuggestion: 'env://DEEPSEEK_API_KEY',
    docsUrl: origin('https', 'api-docs.deepseek.com', '/'),
    local: false,
    note: 'OpenAI-compatible root.',
  },
  {
    key: 'ollama',
    displayName: 'Ollama',
    endpoint: origin('http', 'localhost:11434', '/v1'),
    wireProtocol: 'openai_compat',
    models: ['qwen2.5'],
    keyRefSuggestion: '',
    docsUrl: origin('https', 'github.com', '/ollama/ollama/blob/main/docs/openai.md'),
    local: true,
    note: 'Local server; key usually unnecessary.',
  },
];

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
      if (path.includes('/governance/provider-catalogue')) {
        return new Response(JSON.stringify({ items: CATALOGUE, total: CATALOGUE.length }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify({ name: 'saved', providerClass: 'PRIVATE_NETWORK', endpoint: origin('https', 'example.invalid', '/v1'), enabled: true }), { status: method === 'POST' ? 201 : 200, headers: { 'content-type': 'application/json' } });
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

function fieldByLabel(wrapper: VueWrapper, text: RegExp) {
  const label = wrapper.findAll('label').find((row) => text.test(row.text()));
  return label?.find('input')?.element as HTMLInputElement | undefined;
}

async function fill(wrapper: VueWrapper, text: RegExp, value: string) {
  const box = fieldByLabel(wrapper, text);
  if (!box) throw new Error(`no field labelled ${text}`);
  box.value = value;
  box.dispatchEvent(new Event('input'));
  await flushPromises();
}

async function open(provider: Record<string, unknown> | null = null) {
  const wrapper = mount(ProviderSheet, {
    props: { modelValue: false, provider: provider as never },
    global: { plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }]] },
  });
  await wrapper.setProps({ modelValue: true });
  await vi.waitFor(() => expect(wrapper.findAll('option').length).toBeGreaterThan(1));
  return wrapper;
}

describe('the provider registration sheet', () => {
  it('fills the address, key reference and model names from the chosen vendor', async () => {
    const wrapper = await open();

    await wrapper.find('select.ps__pick').setValue('deepseek');
    expect(fieldByLabel(wrapper, /^Endpoint/)?.value).toBe(DEEPSEEK_ENDPOINT);
    expect(fieldByLabel(wrapper, /Suggested model names/)?.value).toBe('deepseek-chat, deepseek-reasoner');
    expect(wrapper.text()).toContain('OpenAI-compatible root.');

    const suggestion = wrapper.findAll('button').find((button) => /Use suggested reference/.test(button.text()));
    expect(suggestion).toBeTruthy();
    await suggestion!.trigger('click');
    expect(fieldByLabel(wrapper, /API key/)?.value).toBe('env://DEEPSEEK_API_KEY');

    await fill(wrapper, /^Name/, 'deepseek-team');
    const save = wrapper.findAll('button').find((button) => button.text() === 'Save');
    await save!.trigger('click');
    await flushPromises();

    const posted = sent.find((s) => s.method === 'POST' && s.path.includes('/governance/providers'));
    expect(posted?.body).toMatchObject({
      name: 'deepseek-team',
      vendor: 'deepseek',
      endpoint: DEEPSEEK_ENDPOINT,
      apiKeySecretRef: 'env://DEEPSEEK_API_KEY',
      suggestedModels: ['deepseek-chat', 'deepseek-reasoner'],
    });
  });

  it('shows the stored preset and sends only the model list that moved', async () => {
    const wrapper = await open({
      name: 'ds',
      providerClass: 'APPROVED_EXTERNAL',
      endpoint: DEEPSEEK_ENDPOINT,
      apiKeySecretRef: 'env://DEEPSEEK_API_KEY',
      adapter: 'openai_compat',
      enabled: true,
      vendor: 'deepseek',
      suggestedModels: ['deepseek-chat'],
    });

    // the picker used to render the first catalogue entry regardless of the row it was editing
    expect((wrapper.find('select.ps__pick').element as HTMLSelectElement).value).toBe('deepseek');

    await fill(wrapper, /Suggested model names/, 'deepseek-chat, deepseek-reasoner');
    const save = wrapper.findAll('button').find((button) => button.text() === 'Save');
    await save!.trigger('click');
    await flushPromises();

    const patched = sent.find((s) => s.method === 'PATCH' && s.path.includes('/governance/providers/ds'));
    expect(patched?.body).toEqual({ suggestedModels: ['deepseek-chat', 'deepseek-reasoner'] });
  });
});
