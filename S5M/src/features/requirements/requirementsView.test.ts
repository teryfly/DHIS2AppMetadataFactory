/*
 * The screen's own two decisions, checked where nothing else can: that a save is the operation list
 * S2 accepts (a body without `operations` is a 422 nobody can act on), and that a stage S1 no longer
 * offers for editing shows no write control at all (UM-BR-02 — an action that never arrived is
 * unavailable, not assumed so).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query';
import { ref } from 'vue';
import { createPinia } from 'pinia';
import { i18n } from '@/i18n';
import RequirementsView from './RequirementsView.vue';
import RequirementFormSheet from './RequirementFormSheet.vue';

const entriesQ = {
  data: ref<Record<string, unknown> | undefined>(undefined),
  isLoading: ref(false),
  isError: ref(false),
  // the screen re-reads before every write, so the stub answers the way the query does
  refetch: vi.fn(async () => ({ data: entriesQ.data.value })),
};
const stageRow = { data: ref<Record<string, unknown>>({ items: [{ id: 'sta-1', stageType: 'REQUIREMENTS', status: 'READY' }] }) };
const actionsQ = { data: ref<Record<string, unknown> | undefined>(undefined), isSuccess: ref(true) };
const submitted = vi.fn();

vi.mock('@/fui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/fui')>();
  return {
    ...actual,
    FuiCard: {
      props: ['title'],
      emits: ['click'],
      template: '<div class="stub-card"><slot name="title" /><slot name="extra" /><slot /><slot name="footer" /></div>',
    },
    FuiButton: {
      props: ['label', 'disabled', 'loading', 'variant', 'size', 'block'],
      emits: ['click'],
      template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
    },
    FuiAsync: {
      props: ['loading', 'error', 'empty', 'emptyTitle', 'emptyHint', 'emptyAction'],
      emits: ['retry', 'action'],
      template: '<div class="stub-async"><slot v-if="!empty" /><button v-else-if="emptyAction" @click="$emit(\'action\')">{{ emptyAction }}</button></div>',
    },
    FuiNotice: { props: ['tone', 'text'], template: '<div class="stub-notice">{{ text }}<slot /></div>' },
    FuiSegmented: {
      props: ['modelValue', 'options', 'label', 'small'],
      emits: ['update:modelValue'],
      template: '<div class="stub-seg"><button v-for="o in options" :key="o.value" @click="$emit(\'update:modelValue\', o.value)">{{ o.label }}</button></div>',
    },
    FuiStatusTag: { props: ['tone', 'label'], template: '<span class="stub-tag">{{ label }}</span>' },
    FuiDialog: { props: ['show', 'title'], emits: ['confirm', 'update:show'], template: '<div class="stub-dialog"><slot /></div>' },
    FuiSkeleton: { props: ['loading'], template: '<div><slot /></div>' },
    FuiBottomSheet: {
      props: ['modelValue', 'title'],
      emits: ['update:modelValue'],
      template: '<div class="stub-sheet"><slot /><slot name="footer" /></div>',
    },
    FuiActionButton: {
      props: ['label', 'action', 'disabled', 'loading', 'variant', 'size', 'block', 'targetId', 'workflowId'],
      emits: ['click'],
      template: '<button class="stub-action" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
    },
  };
});

// The sheet imports it by path, so the barrel mock above never reaches it.
vi.mock('@/fui/FuiActionButton.vue', () => ({
  default: {
    props: ['label', 'action', 'disabled', 'loading', 'variant', 'size', 'block', 'targetId', 'workflowId'],
    emits: ['click'],
    template: '<button class="stub-action" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

vi.mock('@/api/queries', () => ({
  useBlueprintEntries: () => ({
    get data() {
      return entriesQ.data;
    },
    get isLoading() {
      return entriesQ.isLoading;
    },
    get isError() {
      return entriesQ.isError;
    },
    refetch: entriesQ.refetch,
  }),
  useSectionSchema: () => ({ data: ref(undefined), isLoading: ref(false) }),
}));

vi.mock('@/api/mutations', () => ({
  useSubmitPatch: () => ({
    mutate: (...args: unknown[]) => submitted(...args),
    isPending: ref(false),
    error: ref(null),
    data: ref(undefined),
  }),
}));

vi.mock('@/features/common/useProjectWorkflow', () => ({
  useProjectWorkflow: () => ({ workflowId: ref('wf-1'), stages: stageRow, actions: actionsQ, currentStageId: ref('sta-1') }),
}));

const routes: string[] = [];
vi.mock('vue-router', async (importOriginal) => {
  const real = await importOriginal<typeof import('vue-router')>();
  return {
    ...real,
    useRoute: () => ({ params: { id: 'prj-1' }, query: {} }),
    useRouter: () => ({ push: (to: string) => routes.push(to) }),
  };
});

/** S1's allowed-actions answer, shaped as the wire sends it: one entry naming the stage. */
function allow(action: string, targetId: string | null = null, available = true): void {
  actionsQ.data.value = {
    allowed: available ? [{ action, available, targetId }] : [],
    unavailable: available ? [] : [{ action, available, targetId, reasonCode: 'STAGE_NOT_READY' }],
  };
}

function openAs(items: Record<string, unknown>[]): void {
  entriesQ.data.value = { version: '0.1.0', entries: items, notFound: [] };
}

beforeEach(() => {
  submitted.mockReset();
  vi.clearAllMocks();
  routes.length = 0;
  allow('patch.submit', 'sta-1');
  openAs([
    { businessKey: 'req.a', statement: '第一条', source: { note: 'n' }, _section: 'requirements', _path: '/requirements/items/req.a' },
    { businessKey: 'req.b', statement: '第二条', source: { artifactId: 'art-1' }, actors: ['actor.midwife'] },
  ]);
});

function mounted() {
  return mount(RequirementsView, {
    global: {
      plugins: [createPinia(), i18n, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: 0 } } }) }]],
    },
  });
}

describe('the requirement entry screen', () => {
  it('lists the draft entries without S2’s locators', async () => {
    const view = mounted();
    await flushPromises();
    expect(view.text()).toContain('第一条');
    expect(view.text()).toContain('req.a');
    expect(view.text()).not.toContain('_path');
  });

  it('saves a new entry as the operations S2 reads, against the stage that owns them', async () => {
    const view = mounted();
    await flushPromises();
    view.findAll('.stub-action')[0].trigger('click');
    await flushPromises();
    const sheet = view.findComponent(RequirementFormSheet);
    expect(sheet.exists()).toBe(true);
    sheet.vm.$emit('save', { kind: 'create', item: { businessKey: 'req.c', statement: '第三条', source: { note: 'hand' } } });
    await flushPromises();
    expect(submitted).toHaveBeenCalledTimes(1);
    const [vars] = submitted.mock.calls[0] as [{ stageId: string; projectId: string; body: { patch: { operations: { op: string }[] } } }];
    expect(vars.stageId).toBe('sta-1');
    expect(vars.projectId).toBe('prj-1');
    expect(vars.body.patch.operations.map((o) => o.op)).toEqual(['add']);
    expect(JSON.stringify(vars.body.patch.operations)).toContain('req.c');
  });

  it('refuses to offer any write once S1 stops naming patch.submit for the stage', async () => {
    allow('patch.submit', 'sta-1', false);
    actionsQ.data.value = { allowed: [], unavailable: [] };
    const view = mounted();
    await flushPromises();
    // the sheet is always in the tree (closed), so the write controls that must vanish are the page's
    // own: the one in the header card and the ones in each entry card
    expect(view.findAll('.stub-card .stub-action').length).toBe(0);
    expect(view.text()).toContain('变更请求');
  });

  it('shows the draft, not the approved version, by default and names why the other is read-only', async () => {
    const view = mounted();
    await flushPromises();
    const segments = view.findAll('.stub-seg button');
    segments[1].trigger('click');
    await flushPromises();
    expect(view.text()).toContain('只读');
  });
});
