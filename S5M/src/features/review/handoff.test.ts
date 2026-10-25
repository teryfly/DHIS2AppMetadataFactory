/*
 * UM-REV-007 / CP-UI-021 on the phone: the development package is a listing you can read, and the
 * absence of one is guidance rather than a blank card.
 *
 * Three things this pins down that a screenshot cannot. The first is the download: a `<button>` that
 * only looks like a link leaves the operator with a page and no file, so the address has to be a real
 * `href` carrying `download=true`. The second is the empty arm: S1 answers 404 with
 * `details.reason=NOT_GENERATED`, and the screen must route that into `FuiAsync`'s guided intent
 * instead of drawing an error. The third is UM-BR-01's: whether the package exists is
 * `frontend_package.download`'s answer, and a withheld action must stop the read rather than let the
 * page decide for itself that there is something to fetch.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { i18n } from '@/i18n';

// The refs are created inside the mock factory: `vi.hoisted` runs before any import is
// initialized, so reaching for `ref` up there is reaching for an undefined binding.
const h = vi.hoisted(() => ({
  refs: null as null | {
    pkg: { value: unknown };
    file: { value: unknown };
    pkgIsError: { value: unknown };
    pkgError: { value: unknown };
    actions: { value: unknown };
    pkgEnabled: null | (() => boolean);
  },
}));

const PACKAGE_ACTION = (available: boolean, reasonCode?: string): unknown => ({
  scope: 'workflow',
  allowed: available ? [{ action: 'frontend_package.download', available: true }] : [],
  unavailable: available ? [] : [{ action: 'frontend_package.download', available: false, reasonCode, details: {} }],
});

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'proj-1' } }) }));
vi.mock('@/api/queries', async () => {
  const { ref, toValue } = await import('vue');
  const refs = {
    pkg: ref({}),
    file: ref(''),
    pkgIsError: ref(false),
    pkgError: ref<unknown>(null),
    actions: ref<unknown>(PACKAGE_ACTION(true)),
    pkgEnabled: null as null | (() => boolean),
  };
  h.refs = refs;
  return {
    useFrontendPackage: (_id: unknown, enabled: unknown) => {
      // The view passes the gate through as a `MaybeRefOrGetter`; flattening it to a function here
      // is what lets a test read back whether the page asked to be switched on.
      refs.pkgEnabled = () => toValue(enabled as never) as boolean;
      return { data: refs.pkg, isLoading: ref(false), isError: refs.pkgIsError, error: refs.pkgError, refetch: () => undefined };
    },
    useFrontendPackageFile: () => ({ data: refs.file, isLoading: ref(false), isError: ref(false), error: ref(null) }),
    useWorkflowOfProject: () => ({ data: ref({ id: 'wf-1' }), isLoading: ref(false) }),
    useAllowedActions: () => ({ data: refs.actions, isLoading: ref(false) }),
  };
});
vi.mock('@/fui', () => ({
  FuiCard: { props: ['title'], template: '<section class="c"><h3>{{ title }}</h3><slot /></section>' },
  FuiStatusTag: { props: ['tone', 'label'], template: '<span class="tag">{{ label }}</span>' },
  FuiButton: { props: ['label', 'size', 'block', 'loading'], emits: ['click'], template: '<button @click="$emit(\'click\')">{{ label }}</button>' },
  FuiNotice: { props: ['tone', 'text'], template: '<div class="notice">{{ text }}</div>' },
  FuiAsync: {
    props: ['loading', 'error', 'empty', 'intent', 'emptyTitle', 'emptyHint', 'projectId'],
    emits: ['retry'],
    // Routed the way the real shell routes it: an intent that is not `ok` replaces the content, so a
    // test asserting the listing is absent is asserting the shell, not a rendering accident.
    template:
      '<div class="async"><span class="async__intent">{{ intent ? intent.kind : \'none\' }}</span>' +
      '<template v-if="intent && intent.kind !== \'ok\'"><slot name="fallback" /></template>' +
      '<slot v-else /></div>',
  },
}));

// Importing the view is what evaluates the factory above, so `h.refs` is filled by the time the
// first test body runs.
const HandoffView = (await import('./HandoffView.vue')).default;
const { AppError } = await import('@/api/errors');

function state() {
  if (!h.refs) throw new Error('the query mock never ran');
  return h.refs;
}

beforeEach(() => {
  const r = state();
  r.pkg.value = {};
  r.file.value = '';
  r.pkgIsError.value = false;
  r.pkgError.value = null;
  r.actions.value = PACKAGE_ACTION(true);
  r.pkgEnabled = null;
});

const FILES = {
  projectId: 'proj-1',
  artifactId: 'art-hof',
  blueprintVersion: 'v7',
  files: [
    { path: 'src/api/dataElements.ts', kind: 'CONTRACT', sizeBytes: 2410, contentHash: 'sha256:hf1' },
    { path: 'README.md', kind: 'DOCUMENT', sizeBytes: 1840, contentHash: 'sha256:hf0' },
  ],
};

describe('开发包在手机上（UM-REV-007、CP-UI-021）', () => {
  it('lists S2’s files under S2’s own kind, and offers a real download address', async () => {
    state().pkg.value = FILES;
    const w = mount(HandoffView, { global: { plugins: [i18n] } });
    await flushPromises();
    expect(w.text()).toContain('src/api/dataElements.ts');
    // S2's own kind, in the words the phone shows for it (SRS §2.1) — not the wire token
    expect(w.text()).toContain('接口契约');
    expect(w.text()).toContain('文档');
    const dl = w.findAll('a[href*="download=true"]');
    expect(dl.length).toBe(2);
    expect(dl[0]?.attributes('href')).toContain('/frontend-package/files/src/api/dataElements.ts');
    expect(w.find('.async__intent').text()).toBe('ok');
  });

  it('shows a file’s content only after the row is opened', async () => {
    const r = state();
    r.pkg.value = FILES;
    const w = mount(HandoffView, { global: { plugins: [i18n] } });
    await flushPromises();
    expect(w.text()).not.toContain('export const x');
    r.file.value = 'export const x = 1;\n';
    const buttons = w.findAll('button').filter((b) => b.text() === '查看');
    expect(buttons.length).toBe(2);
    await buttons[0]?.trigger('click');
    await flushPromises();
    expect(w.text()).toContain('export const x = 1;');
  });

  it('routes "no package yet" into the guided empty state, not an error', async () => {
    const r = state();
    r.pkgIsError.value = true;
    r.pkgError.value = new AppError({
      errorCode: 'NOT_FOUND',
      httpStatus: 404,
      message: 'no package',
      details: { reason: 'NOT_GENERATED', requiredStage: 'HANDOFF' },
    });
    const w = mount(HandoffView, { global: { plugins: [i18n] } });
    await flushPromises();
    expect(w.find('.async__intent').text()).toBe('notGenerated');
  });

  it('asks S1 whether the package exists before reading it, and does not read a withheld one', async () => {
    const r = state();
    r.pkg.value = FILES;
    r.actions.value = PACKAGE_ACTION(false, 'PACKAGE_NOT_GENERATED');
    const w = mount(HandoffView, { global: { plugins: [i18n] } });
    await flushPromises();
    // UM-BR-01: the gate is the answer, so the listing query stays switched off and the guided state
    // names the stage that has to deliver the package first.
    expect(r.pkgEnabled?.()).toBe(false);
    expect(w.find('.async__intent').text()).toBe('notGenerated');
    expect(w.text()).not.toContain('src/api/dataElements.ts');
  });

  it('does not promise a stage when S1 withholds the package for another reason', async () => {
    const r = state();
    r.actions.value = PACKAGE_ACTION(false, 'PROJECT_ARCHIVED');
    const w = mount(HandoffView, { global: { plugins: [i18n] } });
    await flushPromises();
    expect(w.find('.async__intent').text()).toBe('notExist');
    expect(w.find('.notice').text()).toContain('项目已归档');
  });
});
