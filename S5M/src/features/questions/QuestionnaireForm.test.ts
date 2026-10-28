import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { ref } from 'vue';
import { i18n } from '@/i18n';
import type * as T from '@/api/types';
import QuestionnaireForm from './QuestionnaireForm.vue';

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
    stageId: 'ST-1',
    suggestions: [
      { id: 'S-1', text: 'Reuse the existing object', rationale: 'No new UID.' },
      { id: 'S-2', text: 'Create a new object', rationale: 'New UID.' },
    ],
    ...overrides,
  };
}

vi.mock('@/api/mutations', () => ({
  useAnswerQuestions: () => ({ mutate, isPending: ref(false) }),
}));

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

function mountForm(questions: T.QuestionView[]) {
  return mount(QuestionnaireForm, {
    props: { questions, workflowId: 'WF-1', stageId: 'ST-1' },
    global: { plugins: [i18n] },
  });
}

beforeEach(() => {
  i18n.global.locale.value = 'en';
  mutate.mockReset();
});

describe('the inline questionnaire', () => {
  it('shows a default recommendation for historical rows without a model flag and adopts it in one click', async () => {
    const wrapper = mountForm([question()]);
    await flushPromises();

    expect(wrapper.text()).toContain('Default recommendation');
    expect(wrapper.text()).toContain('Accept recommended');

    await wrapper.findAll('button').find((button) => button.text().includes('Accept recommended'))?.trigger('click');
    await flushPromises();

    expect(wrapper.findAll('[role="radio"]')[0].attributes('aria-checked')).toBe('true');
    const submit = wrapper.findAll('button').find((button) => button.text().includes('Submit answers'));
    expect(submit?.attributes('disabled')).toBeUndefined();

    await submit?.trigger('click');

    expect(mutate).toHaveBeenCalledWith(
      {
        workflowId: 'WF-1',
        body: { answers: [{ questionId: 'Q-1', suggestionIds: ['S-1'] }] },
      },
      expect.any(Object),
    );
  });

  it('keeps the model’s own recommendation when the flag exists', async () => {
    const wrapper = mountForm([
      question({
        suggestions: [
          { id: 'S-1', text: 'First option', recommended: false },
          { id: 'S-2', text: 'Second option', recommended: true },
        ],
      }),
    ]);
    await flushPromises();

    expect(wrapper.text()).toContain('Recommended');
    expect(wrapper.text()).not.toContain('Default recommendation');

    await wrapper.findAll('button').find((button) => button.text().includes('Accept recommended'))?.trigger('click');
    await flushPromises();
    expect(wrapper.findAll('[role="radio"]')[1].attributes('aria-checked')).toBe('true');
  });
});
