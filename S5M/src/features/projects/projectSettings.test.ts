/*
 * UI-PRJ-004: the settings page is where a project's management attributes are edited and where
 * its deletion is confirmed. The screen's contract with S1 is the PATCH body: an omitted field
 * means "leave it alone", so the test pins that only the edited attribute leaves the phone.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { nextTick, ref } from 'vue';
import { i18n } from '@/i18n';
import ProjectSettingsView from './ProjectSettingsView.vue';

const spies = vi.hoisted(() => ({
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
}));

const routeState = vi.hoisted(() => ({ query: {} as Record<string, string> }));

const project = ref<Record<string, unknown>>({
  id: 'PRJ-1',
  name: 'Old register',
  ownerName: 'Ana',
  description: 'before',
  projectType: 'GREENFIELD',
  versionConstraints: '2.41.x',
  defaultLanguage: 'zh-CN',
  translationLanguages: ['en'],
  workflowId: 'WF-1',
  archived: false,
  targetEnvironments: [],
});

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({ params: { id: 'PRJ-1' }, query: routeState.query }),
}));

vi.mock('@/api/queries', () => ({
  useProject: () => ({ data: project, isLoading: ref(false), refetch: vi.fn() }),
  useProjectSettings: () => ({ data: ref({ gateReminderHours: 24, notificationEmails: [], notificationWebhookRef: null }) }),
  useUidPool: () => ({ data: ref(undefined) }),
}));

function mutation(mutate = vi.fn()) {
  return { mutate, isPending: ref(false) };
}

vi.mock('@/api/mutations', () => ({
  useUpdateTargetEnvironments: () => mutation(),
  useSaveProjectSettings: () => mutation(),
  useArchiveProject: () => mutation(),
  useExportProject: () => mutation(),
  useDeleteProject: () => mutation(spies.deleteProject),
  useUpgradeMode: () => mutation(),
  useImportProject: () => mutation(),
  useUpdateProject: () => mutation(spies.updateProject),
}));

vi.mock('@/fui', () => ({
  FuiAsync: { props: ['loading'], template: '<div><slot /></div>' },
  FuiCard: { props: ['title', 'id'], template: '<section class="stub-card" :data-id="id"><h3>{{ title }}</h3><slot /></section>' },
  FuiStatusTag: { props: ['label'], template: '<span>{{ label }}</span>' },
  FuiButton: {
    props: ['label', 'disabled', 'variant', 'size', 'block'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot>{{ label }}</slot></button>',
  },
  FuiActionButton: {
    props: ['action', 'label', 'disabled', 'loading', 'size', 'variant', 'workflowId', 'targetId', 'platform'],
    emits: ['click', 'blocked'],
    template: '<button :data-action="action" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  FuiBottomSheet: {
    props: ['modelValue', 'title'],
    emits: ['update:modelValue'],
    template: '<div v-if="modelValue" class="stub-sheet">{{ title }}<slot /><slot name="footer" /></div>',
  },
  FuiField: {
    props: ['modelValue', 'label', 'disabled', 'type'],
    emits: ['update:modelValue'],
    template: '<label>{{ label }}<input :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" /></label>',
  },
  FuiSegmented: {
    props: ['modelValue', 'options', 'disabled'],
    emits: ['update:modelValue'],
    template: '<div class="stub-segmented"><button v-for="o in options" :key="o.value" :disabled="disabled" @click="$emit(\'update:modelValue\', o.value)">{{ o.label }}</button></div>',
  },
  FuiCheckChips: {
    props: ['modelValue', 'options', 'disabled'],
    emits: ['update:modelValue'],
    template: '<div class="stub-chips"><button v-for="o in options" :key="o.value">{{ o.label }}</button></div>',
  },
}));

function mountPage() {
  return mount(ProjectSettingsView, { global: { plugins: [i18n] } });
}

beforeEach(() => {
  spies.updateProject.mockReset();
  spies.deleteProject.mockReset();
  routeState.query = {};
  project.value = { ...project.value, archived: false, name: 'Old register', ownerName: 'Ana', description: 'before' };
});

describe('project management attributes (UI-PRJ-004)', () => {
  it('sends only the edited field to PATCH and leaves the rest alone', async () => {
    const wrapper = mountPage();
    await flushPromises();

    const name = wrapper.findAll('input')[0];
    await name.setValue('Renamed register');
    await nextTick();

    const save = wrapper.findAll('button').find((b) => b.attributes('data-action') === 'project.edit');
    expect(save?.attributes('disabled')).toBeUndefined();
    await save?.trigger('click');

    expect(spies.updateProject).toHaveBeenCalledTimes(1);
    expect(spies.updateProject.mock.calls[0][0]).toMatchObject({
      projectId: 'PRJ-1',
      body: { name: 'Renamed register', ownerName: 'Ana', defaultLanguage: 'zh-CN' },
    });
  });

  it('opens the typed-name confirmation and sends DELETE only after the name matches', async () => {
    project.value = { ...project.value, name: 'Old register', archived: true };
    const wrapper = mountPage();
    await flushPromises();

    const del = wrapper.findAll('button').find((b) => b.attributes('data-action') === 'project.delete');
    await del?.trigger('click');
    expect(wrapper.find('.stub-sheet').exists()).toBe(true);

    const confirmBefore = wrapper.findAll('.stub-sheet button').find((b) => b.text() === i18n.global.t('common.confirmDelete'));
    expect(confirmBefore?.attributes('disabled')).toBeDefined();
    expect(spies.deleteProject).not.toHaveBeenCalled();

    await wrapper.find('.stub-sheet input').setValue('Old register');
    await nextTick();
    const confirm = wrapper.findAll('.stub-sheet button').find((b) => b.text() === i18n.global.t('common.confirmDelete'));
    expect(confirm?.attributes('disabled')).toBeUndefined();
    await confirm?.trigger('click');
    expect(spies.deleteProject).toHaveBeenCalledWith('PRJ-1', expect.any(Object));
  });

  it('offers 归档 while the project is live and 删除 only once it is archived (CP-PRJ-007)', async () => {
    project.value = { ...project.value, archived: false };
    const live = mountPage();
    await flushPromises();
    const actions = (wrapper: ReturnType<typeof mountPage>) =>
      wrapper.findAll('button').map((b) => b.attributes('data-action') ?? '');
    expect(actions(live)).toContain('project.archive');
    expect(actions(live)).not.toContain('project.delete');
    live.unmount();

    project.value = { ...project.value, archived: true };
    const retired = mountPage();
    await flushPromises();
    expect(actions(retired)).toContain('project.delete');
    expect(actions(retired)).not.toContain('project.archive');
  });

  it('does not open the delete sheet from a query string', async () => {
    project.value = { ...project.value, archived: true };
    routeState.query = { action: 'delete' };
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.find('.stub-sheet').exists()).toBe(false);
  });
});
