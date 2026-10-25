/*
 * UM-NAV-001 / the approver's queue on a phone. 待办 already aggregates the gates waiting on a
 * person across projects — that is what its badge counts — so the approvals entry in the side nav is
 * an *address*, not a second list. Which means the filter has to survive being put in the URL: an
 * operator who sends a colleague `/todo?kind=gate` must land on the gate rows, and one who taps a
 * filter must be able to go back to where they were.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { i18n } from '@/i18n';
import TodoView from './TodoView.vue';

const route = vi.hoisted(() => ({ query: {} as Record<string, string> }));
const replaced = vi.hoisted(() => [] as unknown[]);

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: () => undefined, replace: (to: unknown) => replaced.push(to) }),
}));

vi.mock('@/api/queries', async () => {
  const { ref } = await import('vue');
  const gates = ref<Record<string, unknown>>({
    items: [
      { id: 'GT-1', type: 'REQUIREMENT_GATE', status: 'WAITING_APPROVAL', projectName: 'ANC Tally', environmentKey: 'env-dev', createdAt: '2026-10-03T08:00:00Z' },
    ],
  });
  const projects = ref<Record<string, unknown>>({
    items: [{ id: 'PRJ-1', name: 'ANC Tally', stages: [{ id: 'ST-1', stageType: 'MAPPING', status: 'BLOCKED' }] }],
  });
  const questions = ref<Record<string, unknown>>({
    items: [
      { id: 'Q-1', text: 'Is the SMS export weekly or monthly?', severity: 'MEDIUM', status: 'OPEN', projectId: 'PRJ-1', stageId: 'ST-1', createdAt: '2026-10-04T08:00:00Z' },
    ],
  });
  return {
    useGatesPending: () => ({ data: gates, isLoading: ref(false) }),
    useOpenQuestions: () => ({ data: questions, isLoading: ref(false) }),
    useProjects: () => ({ data: projects, isLoading: ref(false) }),
  };
});

vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ isSnoozed: () => false, snooze: () => undefined }) }));
vi.mock('@/stores/prefs', () => ({ usePrefsStore: () => ({ language: 'en' }) }));

vi.mock('@/fui', () => ({
  FuiCard: { props: ['title', 'clickable'], template: '<section><slot name="title" /><slot /></section>' },
  FuiStatusTag: { props: ['tone', 'label'], template: '<span>{{ label }}</span>' },
  FuiButton: { props: ['label', 'variant', 'size', 'round'], emits: ['click'], template: '<button @click="$emit(\'click\')">{{ label }}</button>' },
  FuiAsync: { props: ['loading', 'empty', 'emptyTitle', 'emptyHint'], template: '<div><slot v-if="!loading && !empty" /></div>' },
  FuiFilterBar: { props: ['modelValue', 'options'], emits: ['update:modelValue'], template: '<div class="stub-filter"><button v-for="o in options" :key="o.value" @click="$emit(\'update:modelValue\', o.value)">{{ o.label }}</button></div>' },
  FuiSwipeCell: { props: [], template: '<div><slot /><slot name="right" /></div>' },
}));
vi.mock('@/fui/FuiIcon.vue', () => ({ default: { props: ['name', 'size'], template: '<i />' } }));

function mounted() {
  return mount(TodoView, { global: { plugins: [i18n, [VueQueryPlugin, { queryClient: new QueryClient() }]] } });
}

beforeEach(() => {
  i18n.global.locale.value = 'en';
  route.query = {};
  replaced.length = 0;
});

describe('the approval queue inside 待办 (UM-NAV-001)', () => {
  it('opens on the filter the address names', async () => {
    route.query = { kind: 'gate' };
    const wrapper = mounted();
    await flushPromises();
    // one gate row and no blocked stage row: the address decided the filter before anything rendered
    expect(wrapper.text()).toContain('Pending approval · Requirements');
    // the filter bar always offers every kind; what must be gone is the blocked stage's own row
    expect(wrapper.text()).not.toContain('Blocked stages · Mapping');
  });

  it('writes the filter back into the address, so the screen can be handed to somebody', async () => {
    const wrapper = mounted();
    await flushPromises();
    await wrapper.find('.stub-filter button:nth-child(2)').trigger('click');
    expect(replaced).toEqual([{ query: { kind: 'gate' } }]);
  });

  it('drops the parameter when nothing is filtered, and ignores a value it has no list for', async () => {
    route.query = { kind: 'nonsense' };
    const wrapper = mounted();
    await flushPromises();
    expect(wrapper.text()).toContain('Pending approval · Requirements');
    expect(wrapper.text()).toContain('Blocked stages · Mapping');
  });

  it('names the gate by the work it closes instead of the token on the wire', async () => {
    route.query = { kind: 'gate' };
    const wrapper = mounted();
    await flushPromises();
    expect(wrapper.text()).toContain('Requirements');
    expect(wrapper.text()).not.toContain('REQUIREMENT_GATE');
  });

  it('lists open questions as their own to-do category with the project they belong to', async () => {
    route.query = { kind: 'question' };
    const wrapper = mounted();
    await flushPromises();
    expect(wrapper.text()).toContain('Open questions · Is the SMS export weekly or monthly?');
    expect(wrapper.text()).toContain('ANC Tally · Mapping');
    expect(wrapper.text()).toContain('Choose answer');
  });
});
