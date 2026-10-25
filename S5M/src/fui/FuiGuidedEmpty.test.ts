import { describe, expect, it, vi } from 'vitest';
import { ref } from 'vue';
import { mount } from '@vue/test-utils';
import FuiGuidedEmpty from './FuiGuidedEmpty.vue';

// The stage lookup is a vue-query composition; stubbing it keeps this test about what the reader
// sees (UM-AC-19 wording and the jump target), not about the query layer.
const jump = vi.hoisted(() => ({ to: '', stageType: '', available: true, pending: false }));

vi.mock('@/lib/stageJump', () => ({
  useRequiredStageJump: () => ({
    to: ref(jump.to),
    stageType: ref(jump.stageType),
    available: ref(jump.available),
    pending: ref(jump.pending),
  }),
}));

const push = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }));

// Vant components are resolved by the global install in main.ts, which a unit mount does not have;
// these stubs keep the assertions on rendered text rather than on Vant internals.
const VAN_STUBS = {
  'van-empty': { props: ['description'], template: '<div class="stub-empty">{{ description }}<slot /></div>' },
  'van-icon': { props: ['name', 'size', 'color'], template: '<i />' },
  'van-button': { props: ['type', 'size', 'round', 'plain', 'loading', 'disabled', 'icon', 'block'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
};

function render(intent: Record<string, unknown>) {
  return mount(FuiGuidedEmpty, {
    props: { intent: intent as never, projectId: 'p1' },
    global: { stubs: VAN_STUBS },
  });
}

describe('UM-AC-19 · 引导式空状态', () => {
  it('names the stage in the sentence and on the button, and navigates there', async () => {
    Object.assign(jump, { to: '/stages/s-7', stageType: 'BASELINE_COMPILE', available: true, pending: false });
    const w = render({ kind: 'notGenerated', requiredStage: 'BASELINE_COMPILE', surface: 'graph' });
    expect(w.text()).toContain('完成基线编译后可查看');
    expect(w.text()).toContain('去基线编译');
    await w.find('button').trigger('click');
    expect(push).toHaveBeenCalledWith('/stages/s-7');
  });

  it('does not offer a dead button when this workflow has no such stage', () => {
    Object.assign(jump, { to: '', available: false });
    const w = render({ kind: 'notGenerated', requiredStage: 'HANDOFF', surface: 'package' });
    expect(w.findAll('button').length).toBe(0);
    expect(w.text()).toContain('当前流程没有这个阶段');
  });

  it('says 内容不存在 for NOT_EXIST, with no stage promised', () => {
    const w = render({ kind: 'notExist', surface: 'graph' });
    expect(w.text()).toContain('内容不存在');
    expect(w.text()).not.toContain('完成');
  });

  it('falls back to a generic line when S1 gives no requiredStage', () => {
    const w = render({ kind: 'notGenerated', surface: 'graph' });
    expect(w.text()).toContain('该阶段完成后即可查看');
  });

  it('shows the server message for a real error', () => {
    const err = Object.assign(new Error('boom'), { name: 'AppError', errorCode: 'INTERNAL_ERROR', httpStatus: 500, details: {} });
    const w = render({ kind: 'error', surface: 'graph', error: err });
    expect(w.text()).toContain('boom');
  });

  it('carries the page’s own line under a guided state that is not an error', () => {
    // The Handoff page answers “S1 withheld this resource for a reason that is not “not yet
    // generated”” (an archived project, say) with S1's reason under the guidance. The slot used to
    // be wired only to the error branch, so that sentence existed in the page and nowhere on screen.
    const w = mount(FuiGuidedEmpty, {
      props: { intent: { kind: 'notExist', surface: 'package' } as never, projectId: 'p1' },
      global: { stubs: VAN_STUBS },
      slots: { fallback: '<p class="why">项目已归档</p>' },
    });
    expect(w.find('.why').exists()).toBe(true);
    expect(w.text()).toContain('项目已归档');
  });
});
