import { describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { ref } from 'vue';
import { i18n } from '@/i18n';
import type * as T from '@/api/types';
import RunLogSheet from './RunLogSheet.vue';

vi.mock('@/api/queries', () => ({
  useAgentRun: () => ({ data: ref(null), isLoading: ref(false) }),
}));
vi.mock('@/stores/prefs', () => ({ usePrefsStore: () => ({ language: 'en' }) }));
vi.mock('@/stores/progress', () => ({
  useProgressStore: () => ({ get: () => undefined }),
}));
vi.mock('@/fui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/fui')>();
  return {
    ...actual,
    FuiBottomSheet: {
      props: ['modelValue', 'title'],
      template: '<section><h2>{{ title }}</h2><slot /></section>',
    },
  };
});

function run(attempt: number, status: string, message: string): T.StageRunView {
  return {
    id: `srn-${attempt}`,
    attempt,
    agentRunIds: [`arun-${attempt}`],
    result: { status, summary: `summary-${attempt}` },
    createdAt: `2026-10-26T08:0${attempt}:00Z`,
    completedAt: `2026-10-26T08:0${attempt}:30Z`,
    log: [
      {
        seq: attempt,
        at: `2026-10-26T08:0${attempt}:10Z`,
        eventType: 'agent.progress',
        step: 'model_call',
        message,
        fraction: 0.3,
      },
    ],
  };
}

const stage = {
  id: 'ST-1',
  workflowId: 'WF-1',
  stageType: 'DOMAIN',
  status: 'FAILED',
  stageRuns: [run(1, 'WAITING_INPUT', 'first-run-step'), run(2, 'FAILED', 'second-run-step'), run(3, 'FAILED', 'third-run-step')],
} as unknown as T.StageDetailView;

function mountSheet() {
  return mount(RunLogSheet, {
    props: { modelValue: true, workflowId: 'WF-1', stageId: 'ST-1', stage },
    global: { plugins: [i18n] },
  });
}

describe('the mobile run log', () => {
  it('lists every attempt, selects the newest by default and switches to an older log', async () => {
    i18n.global.locale.value = 'en';
    const wrapper = mountSheet();
    await flushPromises();

    const buttons = wrapper.findAll('button.rl__run-item');
    expect(buttons).toHaveLength(3);
    expect(buttons[0].text()).toContain('Attempt 3');
    expect(buttons[0].classes()).toContain('rl__run-item--active');
    expect(wrapper.text()).toContain('third-run-step');
    expect(wrapper.text()).not.toContain('first-run-step');

    await buttons[2].trigger('click');
    await flushPromises();

    expect(buttons[2].classes()).toContain('rl__run-item--active');
    expect(wrapper.text()).toContain('first-run-step');
    expect(wrapper.text()).toContain('summary-1');
    expect(wrapper.text()).not.toContain('third-run-step');
  });
});
