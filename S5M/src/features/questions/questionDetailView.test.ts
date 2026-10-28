import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { ref } from 'vue';
import { i18n } from '@/i18n';
import type * as T from '@/api/types';
import QuestionDetailView from './QuestionDetailView.vue';

const route = vi.hoisted(() => ({ params: { id: 'Q-1' } }));
const push = vi.hoisted(() => vi.fn());
const mutate = vi.hoisted(() => vi.fn());

function question(overrides: Partial<T.QuestionView> = {}): T.QuestionView {
  return {
    id: 'Q-1',
    text: 'Which option?',
    severity: 'HIGH',
    answerMode: 'SINGLE',
    blocking: true,
    status: 'OPEN',
    workflowId: 'WF-1',
    projectId: 'P-1',
    stageId: 'ST-1',
    suggestions: [
      { id: 'S-1', text: 'First option', recommended: false },
      { id: 'S-2', text: 'Second option', recommended: true },
    ],
    ...overrides,
  };
}

const first = question({ id: 'Q-1', text: 'First question?', suggestions: [
  { id: 'S-1', text: 'First option', recommended: false },
  { id: 'S-2', text: 'Second option', recommended: true },
] });
const second = question({ id: 'Q-2', text: 'Second question?', suggestions: [
  { id: 'S-3', text: 'Third option', recommended: false },
  { id: 'S-4', text: 'Fourth option', recommended: true },
] });

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push }),
}));

vi.mock('@/api/queries', async () => ({
  useQuestion: () => ({ data: ref(first), isLoading: ref(false), isError: ref(false), refetch: vi.fn() }),
  useQuestions: () => ({ data: ref({ items: [first, second] }), isLoading: ref(false), isError: ref(false), refetch: vi.fn() }),
}));

vi.mock('@/api/mutations', () => ({
  useAnswerQuestions: () => ({ mutate, isPending: ref(false) }),
}));

vi.mock('@/sse/useWorkflowEvents', () => ({ useWorkflowEvents: () => undefined }));
vi.mock('@/lib/toast', () => ({ pushError: vi.fn(), pushInfo: vi.fn() }));

vi.mock('@/fui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/fui')>();
  return {
    ...actual,
    FuiActionButton: {
      props: ['label', 'disabled', 'loading'],
      emits: ['click'],
      template: '<button type="button" :disabled="disabled || loading" @click="$emit(\'click\')">{{ label }}</button>',
    },
  };
});

function mountView() {
  return mount(QuestionDetailView, { global: { plugins: [i18n] } });
}

beforeEach(() => {
  i18n.global.locale.value = 'en';
  push.mockReset();
  mutate.mockReset();
});

describe('the mobile questionnaire', () => {
  it('keeps one submit enabled after any answer and adopts every recommendation in one click', async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain('First question?');
    expect(wrapper.text()).toContain('Second question?');

    const submit = wrapper.findAll('button').find((button) => button.text().includes('Submit answers'));
    expect(submit?.attributes('disabled')).toBeDefined();

    const radios = wrapper.findAll('[role="radio"]');
    await radios[1].trigger('click');
    await flushPromises();

    expect(wrapper.text()).toContain('1 blocking question(s) unanswered');
    expect(
      wrapper.findAll('button').find((button) => button.text().includes('Submit answers'))?.attributes('disabled'),
    ).toBeUndefined();

    await wrapper.findAll('button').find((button) => button.text().includes('Accept recommended'))?.trigger('click');
    await flushPromises();

    expect(wrapper.findAll('[role="radio"]')[1].attributes('aria-checked')).toBe('true');
    expect(wrapper.findAll('[role="radio"]')[4].attributes('aria-checked')).toBe('true');

    await wrapper.findAll('button').find((button) => button.text().includes('Submit answers'))?.trigger('click');

    expect(mutate).toHaveBeenCalledWith(
      {
        workflowId: 'WF-1',
        body: {
          answers: [
            { questionId: 'Q-1', suggestionIds: ['S-2'] },
            { questionId: 'Q-2', suggestionIds: ['S-4'] },
          ],
        },
      },
      expect.any(Object),
    );
  });
});
