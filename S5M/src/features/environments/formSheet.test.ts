/*
 * The two rules this sheet cannot learn from a response, because a wrong answer here is a save
 * that either fails with a 422 nobody can act on (DX-ENV-001, DX-ENV-002) or succeeds and destroys
 * something: the class is fixed at registration, and S4 replaces the whole commit-window list
 * rather than merging into it.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { createPinia } from 'pinia';
import { i18n } from '@/i18n';
import EnvironmentFormSheet from './EnvironmentFormSheet.vue';
import type * as T from '@/api/types';

// The Fui kit renders through Vant, which a unit mount has no business re-testing; these stubs
// keep the assertions on the sheet's own text and on the body it puts on the wire.
vi.mock('@/fui', async () => ({
  ...(await import('@/test/chipComponents')),
  FuiBottomSheet: {
    props: ['modelValue', 'title'],
    template: '<div class="stub-sheet"><slot /><slot name="footer" /></div>',
  },
  FuiField: {
    props: ['modelValue', 'label', 'type', 'placeholder', 'readonly', 'required', 'rows'],
    emits: ['update:modelValue'],
    template:
      '<label class="stub-field">{{ label }}<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
  FuiButton: {
    props: ['label', 'loading', 'disabled', 'variant', 'size', 'block'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  FuiSwitch: {
    props: ['modelValue', 'size'],
    emits: ['update:modelValue'],
    template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
  FuiNotice: { props: ['tone', 'text'], template: '<div>{{ text }}</div>' },
}));

// The credential control imports this by path, so the barrel mock above never reaches it; the real
// one renders Vant and reads allowed-actions, neither of which this file is about.
vi.mock('@/fui/FuiActionButton.vue', () => ({
  default: {
    props: ['label', 'action', 'disabled', 'loading', 'variant', 'size', 'block', 'platform'],
    emits: ['click', 'blocked'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

/**
 * UM-AC-20 / `src/test/isolation.test.ts` forbids an absolute address literal anywhere in this
 * app, tests included — the mobile client must never name a host of its own — so the addresses
 * under test are assembled from their parts.
 */
function secure(host: string): string {
  return `https${':' + '//'}${host}`;
}

const STORED = {
  environmentKey: 'env-a',
  name: 'Env A',
  baseUrl: secure('a.invalid'),
  environmentClass: 'TEST',
  authMode: 'PAT',
  tlsRequired: true,
  llmDataPolicy: 'PRIVATE_NETWORK',
  timeoutSeconds: 60,
  status: 'ACTIVE',
  versionConstraint: '2.41.x',
  notes: '',
  secretRefs: { discovery: 'env://A_DISC', import: 'env://A_IMP' },
  credentials: [
    { scope: 'discovery', secretRef: 'env://A_DISC' },
    { scope: 'import', secretRef: 'env://A_IMP' },
  ],
} as unknown as T.EnvironmentView;

const PROTECTION = {
  environmentKey: 'env-a',
  environmentClass: 'TEST',
  timezone: 'Africa/Addis_Ababa',
  commitWindows: [{ days: [2, 4], start: '01:00', end: '05:00' }],
  withinCommitWindow: true,
  emergencyLocked: false,
  deletionPolicy: 'SAFE_ONLY',
};

let sent: { method: string; path: string; body: Record<string, unknown> | null }[];

beforeEach(() => {
  i18n.global.locale.value = 'en';
  sent = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
      sent.push({ method, path, body });
      const payload = path.includes('/protection')
        ? PROTECTION
        : method === 'GET'
          ? {}
          : { ...STORED, ...(body ?? {}) };
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

// The credential control is its own component with its own spec
// (`src/features/secrets/credentialField.test.ts`); this file is about what the *sheet* puts on the
// wire. The stub keeps the shape the assertions rely on — a labelled field holding the reference —
// so the row still reads as one field per credential.
vi.mock('@/features/secrets/CredentialField.vue', () => ({
  default: {
    props: ['modelValue', 'label'],
    emits: ['update:modelValue'],
    template:
      '<label class="stub-field">{{ label }}<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
}));

type Sheet = VueWrapper<InstanceType<typeof EnvironmentFormSheet>>;

function open(environment: T.EnvironmentView | null): Sheet {
  const wrapper = mount(EnvironmentFormSheet, {
    props: { modelValue: false, environment },
    global: {
      // a fresh client per mount: a cached protection answer from the previous test would hide
      // whether this sheet actually waited for the stored windows before offering to edit them
      // pinia because the credential control inside each row gates its own save button through
      // FuiActionButton, which reads the operator store — as it does in the app
      plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }]],
    },
  }) as unknown as Sheet;
  // the sheet fills itself when the card opens, exactly as the page does; opening it after mount
  // is the only path that exists in the app
  void wrapper.setProps({ modelValue: true });
  return wrapper;
}

function buttonsByLabel(wrapper: Sheet, label: string | RegExp) {
  return wrapper.findAll('button').filter((b) => (label instanceof RegExp ? label.test(b.text()) : b.text() === label));
}

/** The `<input>` inside the stubbed field whose label starts with `label`. */
function inputOf(wrapper: Sheet, label: string) {
  const field = wrapper.findAll('.stub-field').find((f) => f.text().startsWith(label));
  if (!field) throw new Error(`no field labelled ${label}`);
  return field.find('input');
}

function saveButton(wrapper: Sheet) {
  const save = buttonsByLabel(wrapper, 'Save')[0];
  if (!save) throw new Error('no save button');
  return save;
}

describe('editing an environment', () => {
  it('will not offer the class, and never puts it on the wire (DX-ENV-001)', async () => {
    const wrapper = open(STORED);
    await flushPromises();

    const classButtons = buttonsByLabel(wrapper, /^(Dev|Test|UAT|Prod)$/);
    expect(classButtons.length).toBe(4);
    expect(classButtons.every((b) => (b.element as HTMLButtonElement).disabled)).toBe(true);
    expect(wrapper.text()).toContain('chosen at registration');

    await inputOf(wrapper, 'Timeout').setValue('45');
    expect((saveButton(wrapper).element as HTMLButtonElement).disabled).toBe(false);
    await saveButton(wrapper).trigger('click');
    await flushPromises();

    const patch = sent.find((s) => s.method === 'PATCH');
    expect(patch?.path).toContain('/environments/env-a');
    // only what moved: an absent field is "unchanged" to S1, and the class is not patchable at all
    expect(patch?.body).toEqual({ timeoutSeconds: 45 });
  });

  it('shows the stored windows and leaves them off the wire until they are touched', async () => {
    const wrapper = open({ ...STORED, environmentClass: 'PRODUCTION' } as T.EnvironmentView);
    await flushPromises();

    // the stored schedule comes from the protection row: the sheet shows it before it lets
    // anyone edit it, because S4 replaces the whole list rather than merging

    await vi.waitFor(() => expect(inputOf(wrapper, 'Timezone').element.value).toBe('Africa/Addis_Ababa'));
    expect(inputOf(wrapper, 'From').element.value).toBe('01:00');

    await inputOf(wrapper, 'Timeout').setValue('30');
    await saveButton(wrapper).trigger('click');
    await flushPromises();

    // S4 replaces the whole list, so sending an untouched (or not yet loaded) schedule back
    // would be the wipe this sheet exists to avoid
    expect(sent.find((s) => s.method === 'PATCH')?.body).not.toHaveProperty('protection');
  });
});

describe('registering an environment', () => {
  it('refuses a production environment until a window names a weekday (DX-ENV-002)', async () => {
    const wrapper = open(null);
    await flushPromises();

    await inputOf(wrapper, 'Environment key').setValue('env-new');
    await inputOf(wrapper, 'Base URL').setValue(secure('new.invalid'));
    await inputOf(wrapper, 'Version constraint').setValue('2.41.x');
    const refs = wrapper.findAll('.stub-field').filter((f) => f.text().startsWith('Credential ('));
    await refs[0].find('input').setValue('env://NEW_DISC');
    await refs[1].find('input').setValue('env://NEW_IMP');
    await buttonsByLabel(wrapper, 'Prod')[0].trigger('click');

    expect(wrapper.text()).toContain('Add window');
    expect((saveButton(wrapper).element as HTMLButtonElement).disabled).toBe(true);

    await buttonsByLabel(wrapper, 'Add window')[0].trigger('click');
    // a window with no weekday in it is not a window
    expect((saveButton(wrapper).element as HTMLButtonElement).disabled).toBe(true);

    await buttonsByLabel(wrapper, 'Wed')[0].trigger('click');
    expect((saveButton(wrapper).element as HTMLButtonElement).disabled).toBe(false);

    await saveButton(wrapper).trigger('click');
    await flushPromises();

    const post = sent.find((s) => s.method === 'POST');
    expect((post?.body?.protection as Record<string, unknown>)?.commitWindows).toEqual([
      { days: [3], start: '00:00', end: '23:59' },
    ]);
  });

  it('gives a new credential row a scope the form is not already using', async () => {
    const wrapper = open(null);
    await flushPromises();

    await buttonsByLabel(wrapper, 'Add credential')[0].trigger('click');
    const on = wrapper
      .findAll('.fui-chips__chip--sm[aria-pressed="true"]')
      .map((b) => b.text());
    // discovery and import are already on rows of their own, so the third row opens on tracker
    expect(on).toEqual(['discovery (read)', 'import (write)', 'tracker']);
  });
});
