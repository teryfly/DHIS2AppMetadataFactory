/*
 * UI-ADM-003 on mobile. The sheet exists because S1 answers a body without `change` with a 422 —
 * which is what the old one sent — and because the classes it offers have to come from the
 * difference between the ceiling and the current policy, not from a list typed into the view.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { i18n } from '@/i18n';
import GovernanceView from './GovernanceView.vue';

// the page reads its own section out of the URL, and the relaxation opener lives in the policy one
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: () => undefined }),
  useRoute: () => ({ path: '/governance/policy', params: { section: 'policy' } }),
}));

vi.mock('@/fui', async () => ({
  ...(await import('@/test/chipComponents')),
  FuiCard: { props: ['title'], template: '<section><h3>{{ title }}</h3><slot /><slot name="extra" /></section>' },
  FuiStatusTag: { props: ['tone', 'label'], template: '<span>{{ label }}</span>' },
  FuiAsync: { props: ['loading', 'empty', 'emptyTitle'], template: '<div><slot v-if="!loading && !empty" /><p v-else-if="empty">{{ emptyTitle }}</p></div>' },
  FuiBottomSheet: {
    props: ['modelValue', 'title'],
    // rendered only while open: every sheet in this view lives in the same tree, and a stub that
    // always rendered its slots would let one sheet's buttons answer another sheet's assertion
    template: '<div v-if="modelValue" class="stub-sheet"><slot /><slot name="footer" /></div>',
  },
  FuiActionButton: {
    props: ['action', 'label', 'platform', 'size', 'variant', 'disabled'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  FuiButton: {
    props: ['label', 'loading', 'disabled', 'variant', 'size', 'block'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  FuiField: {
    props: ['modelValue', 'label', 'type', 'placeholder', 'readonly', 'required', 'rows'],
    emits: ['update:modelValue'],
    template: '<label>{{ label }}<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
  FuiCell: { props: ['title'], template: '<div>{{ title }}<slot /></div>' },
  FuiSwitch: { props: ['modelValue', 'size'], emits: ['update:modelValue'], template: '<input type="checkbox" />' },
  FuiNotice: { props: ['tone', 'text'], template: '<div>{{ text }}</div>' },
  FuiChecklist: { props: ['items'], template: '<ul><li v-for="i in items" :key="i.key">{{ i.label }}</li></ul>' },
  FuiInput: { props: ['modelValue'], emits: ['update:modelValue'], template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
}));

const POLICY = {
  effective: {
    allowed: {
      PUBLIC: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      INTERNAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      CONFIDENTIAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      RESTRICTED: ['LOCAL_ONLY'],
      PHI_PII: [],
    },
    sources: [],
    warnings: [],
  },
  ceiling: {
    allowed: {
      PUBLIC: ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL', 'UNRESTRICTED'],
      INTERNAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL'],
      CONFIDENTIAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      RESTRICTED: ['LOCAL_ONLY'],
      PHI_PII: [],
    },
    sources: ['ceiling'],
    warnings: [],
  },
  isDefault: true,
};

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
      const payload = path.includes('/governance/policy') && method === 'GET' ? POLICY : {};
      return new Response(JSON.stringify(payload), {
        status: method === 'POST' ? 201 : 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function render() {
  return mount(GovernanceView, {
    global: { plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }]] },
  });
}

function buttonByText(wrapper: ReturnType<typeof render>, text: string | RegExp) {
  return wrapper
    .findAll('button')
    .filter((b) => (text instanceof RegExp ? text.test(b.text()) : b.text() === text))
    .map((b) => b.element as HTMLButtonElement);
}

async function openSheet(wrapper: ReturnType<typeof render>) {
  await vi.waitFor(() => expect(buttonByText(wrapper, 'Relax policy').length).toBe(1));
  await buttonByText(wrapper, 'Relax policy')[0]!.click();
}

describe('the relaxation sheet', () => {
  it('offers only the classes the ceiling still allows, and sends them with the reason', async () => {
    const wrapper = render();
    await flushPromises();
    await openSheet(wrapper);

    // PUBLIC and INTERNAL both have headroom; the classes already allowed are not offered again
    expect(wrapper.text()).toContain('Approved external');
    expect(buttonByText(wrapper, /^Local only$/)).toHaveLength(0);

    const confirm = buttonByText(wrapper, 'Confirm')[0];
    expect(confirm?.disabled).toBe(true);

    await buttonByText(wrapper, 'Approved external')[0]!.click();
    const reason = wrapper.findAll('label').find((l) => l.text().startsWith('Relaxation reason'))?.find('input');
    await reason?.setValue('external parsing was reviewed');

    await vi.waitFor(() => expect(buttonByText(wrapper, 'Confirm')[0]?.disabled).toBe(false));
    await buttonByText(wrapper, 'Confirm')[0]!.click();
    await flushPromises();

    const posted = sent.find((s) => s.method === 'POST' && s.path.includes('/governance/policy/relaxations'));
    expect((posted?.body as Record<string, unknown>)?.change).toEqual({ level: 'PUBLIC', addClasses: ['APPROVED_EXTERNAL'] });
    expect((posted?.body as Record<string, unknown>)?.scope).toBe('organization');
    expect(String((posted?.body as Record<string, unknown>)?.reason)).toContain('reviewed');
  });

  it('says patient data is never sendable instead of offering a class it must refuse', async () => {
    const wrapper = render();
    await flushPromises();
    await openSheet(wrapper);

    await buttonByText(wrapper, 'Health data / personal data')[0]!.click();
    expect(wrapper.text()).toContain('may never be sent to a model');
    expect(buttonByText(wrapper, 'Confirm')[0]?.disabled).toBe(true);
    expect(sent.some((s) => s.method === 'POST')).toBe(false);
  });
});
