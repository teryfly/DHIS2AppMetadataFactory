/*
 * S5IF-SEC01 / UM-ADM-002 as amended. The promise under test is narrow: the credential value is a
 * pass-through. It is typed into a masked field, sent once, and then nothing in the browser holds
 * it — no read-back, no mask, no length, nothing in localStorage.
 *
 * These are the four ways a component can "work" while breaking that promise, so each gets its own
 * case: an input that is not masked, a value left in the field after the request, a value that
 * survives into a failure path, and a value that gets composed wrongly for Basic authentication
 * (S4 needs one string shaped `user:password`, not two).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { createPinia } from 'pinia';
import { i18n } from '@/i18n';
import CredentialField from './CredentialField.vue';

// CredentialField imports each control by path, so a barrel mock would never reach it; Vant renders
// nothing this file cares about, so all three are stubbed at the path the component names.
vi.mock('@/fui/FuiField.vue', () => ({
  default: {
    props: ['modelValue', 'label', 'type', 'placeholder', 'disabled', 'autocomplete', 'error'],
    emits: ['update:modelValue'],
    template:
      '<label class="stub-field">{{ label }}<input :type="type || \'text\'" :autocomplete="autocomplete" :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
}));
vi.mock('@/fui/FuiButton.vue', () => ({
  default: {
    props: ['label', 'disabled', 'size'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

vi.mock('@/fui/FuiActionButton.vue', () => ({
  default: {
    props: ['label', 'action', 'disabled', 'loading', 'platform', 'variant', 'size', 'block'],
    emits: ['click', 'blocked'],
    template: '<button :disabled="disabled" data-action="save" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

const SECRET = 'tok-SecRet-9f3a';
const captured: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];

/**
 * `client/http.ts` reads a failure through `res.clone().json()`, so the fake has to be clonable or
 * the error body never reaches `toApiError` and the component can only say "failed" — which is
 * exactly the message this file must be able to tell apart from "no master key".
 */
function answer(status: number, body: unknown): Response {
  const response = {
    status,
    ok: status < 400,
    headers: {
      get: (name: string) => (name.toLowerCase() === 'content-type' ? 'application/json' : null),
      forEach: () => undefined,
    },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
  (response as { clone: () => Response }).clone = () => answer(status, body);
  return response;
}

/** GET /secrets feeds the picker and stays empty; every other call is the write under test. */
function stubFetch(outcome: () => { status: number; body: unknown }): void {
  captured.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method !== 'GET') {
        captured.push({
          url: String(input),
          method,
          body: typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {},
        });
        const { status, body } = outcome();
        return answer(status, body);
      }
      return answer(200, { items: [] });
    }),
  );
}

function open(shape?: 'basic'): { wrapper: ReturnType<typeof mount> } {
  const wrapper = mount(CredentialField, {
    props: { modelValue: '', label: 'Credential (Secret reference)', suggestedName: 'dhis2-dev-discovery', ...(shape ? { shape } : {}) },
    global: {
      plugins: [
        createPinia(),
        i18n,
        [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }],
      ],
    },
  });
  return { wrapper };
}

function field(wrapper: ReturnType<typeof mount>, startsWith: string) {
  const found = wrapper.findAll('.stub-field').find((f) => f.text().startsWith(startsWith));
  if (!found) throw new Error(`no field labelled ${startsWith}`);
  return found.find('input');
}

async function openPanel(wrapper: ReturnType<typeof mount>): Promise<void> {
  const toggle = wrapper.findAll('button').find((b) => /Enter a new credential/i.test(b.text()));
  if (!toggle) throw new Error(`the entry control is missing; buttons=${JSON.stringify(wrapper.findAll('button').map((b) => b.text()))} text=${wrapper.text().slice(0, 200)}`);
  await toggle.trigger('click');
}

beforeEach(() => {
  i18n.global.locale.value = 'en';
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe('CredentialField (S5IF-SEC01)', () => {
  it('masks the value while it is typed and never offers to remember it', async () => {
    const { wrapper } = open();
    await openPanel(wrapper);
    const token = field(wrapper, 'Token or API key');
    expect(token.attributes('type')).toBe('password');
    expect(token.attributes('autocomplete')).toBe('new-password');
  });

  it('sends the value once, then keeps only the reference', async () => {
    stubFetch(() => ({
      status: 200,
      body: { name: 'dhis2-dev-discovery', ref: 'db://dhis2-dev-discovery', algo: 'aes-256-gcm+hkdf-sha256', createdAt: null, updatedAt: null, rotatedAt: null, createdBy: 'ada' },
    }));
    const { wrapper } = open();
    await openPanel(wrapper);
    await field(wrapper, 'Token or API key').setValue(SECRET);
    const save = wrapper.findAll('button').find((b) => b.attributes('data-action') === 'save');
    await save?.trigger('click');
    await flushPromises();

    expect(captured).toHaveLength(1);
    expect(captured[0].url).toContain('/secrets/dhis2-dev-discovery');
    expect(captured[0].body).toEqual({ material: SECRET });
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['db://dhis2-dev-discovery']);
    // the panel closed and its fields are gone with it — nothing still holds the token
    expect(wrapper.text()).not.toContain(SECRET);
  });

  it('forgets the value when the store refuses, and says which refusal it was', async () => {
    stubFetch(() => ({
      status: 503,
      body: { errorCode: 'SECRET_UNAVAILABLE', message: 'no master key', details: { reason: 'storeDisabled' } },
    }));
    const { wrapper } = open();
    await openPanel(wrapper);
    await field(wrapper, 'Token or API key').setValue(SECRET);
    await wrapper.findAll('button').find((b) => b.attributes('data-action') === 'save')?.trigger('click');
    await flushPromises();

    expect(field(wrapper, 'Token or API key').element.value).toBe('');
    expect(wrapper.text()).toContain('FACTORY_CREDENTIAL_MASTER_KEY');
    expect(wrapper.text()).not.toContain(SECRET);
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });

  it('composes user:password for a Basic environment', async () => {
    stubFetch(() => ({ status: 200, body: { name: 'x', ref: 'db://x', algo: 'a', createdAt: null, updatedAt: null, rotatedAt: null, createdBy: 'ada' } }));
    const { wrapper } = open('basic');
    await openPanel(wrapper);
    await field(wrapper, 'DHIS2 username').setValue('admin');
    const password = field(wrapper, 'Password');
    expect(password.attributes('type')).toBe('password');
    await password.setValue('p4ss');
    await wrapper.findAll('button').find((b) => b.attributes('data-action') === 'save')?.trigger('click');
    await flushPromises();

    expect(captured[0].body).toEqual({ material: 'admin:p4ss' });
  });

  it('keeps nothing in browser storage', async () => {
    stubFetch(() => ({ status: 200, body: { name: 'x', ref: 'db://x', algo: 'a', createdAt: null, updatedAt: null, rotatedAt: null, createdBy: 'ada' } }));
    const { wrapper } = open();
    await openPanel(wrapper);
    await field(wrapper, 'Token or API key').setValue(SECRET);
    // The idempotency key lives in sessionStorage and is minted from the route and the name only —
    // hashing the value into the intent would leave a crackable fingerprint of it there.
    expect(JSON.stringify([localStorage, sessionStorage])).not.toContain(SECRET);
  });
});
