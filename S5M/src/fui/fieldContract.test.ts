/*
 * `FuiField` is the one FUI component whose contract is "everything you pass it reaches the Vant
 * field" — and that contract is invisible to every feature test, because they stub this component by
 * path to keep Vant out of the unit tree. When it grew a wrapper div for its hint line, Vue started
 * handing `v-model`, `label`, `type` and the listeners to the wrapper instead of the field: the app
 * kept rendering, every form silently stopped collecting input, and the first symptom was a wizard
 * whose next button never enabled.
 *
 * So this file mounts the real component — no stubs — and asserts the fall-through itself.
 */
import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import FuiField from './FuiField.vue';

function typeInto(wrapper: ReturnType<typeof mount>, value: string): void {
  const input = wrapper.find('input');
  if (!input.exists()) throw new Error('FuiField rendered no input — the props are not reaching Vant');
  input.setValue(value);
}

describe('UM-TECH-001 · FuiField 的透传契约', () => {
  it('puts v-model on the field, not on the wrapper', () => {
    // the parent's `update:modelValue` listener arrives as an attribute here, and an attribute
    // handler is not a component event: `wrapper.emitted()` cannot see it. A spy is the only way to
    // assert the listener actually reached the Vant field rather than the wrapper div.
    const onInput = vi.fn();
    const wrapper = mount(FuiField, { props: { modelValue: 'hi', label: '名称', 'onUpdate:modelValue': onInput } });
    // the label has to reach Vant as well: it is what `getByLabel` and the a11y tree use
    expect(wrapper.text()).toContain('名称');
    expect(wrapper.find('input').element.value).toBe('hi');

    typeInto(wrapper, '端到端测试项目');
    expect(onInput).toHaveBeenCalledWith('端到端测试项目');
  });

  it('renders the hint as text a person can read', () => {
    const withHelp = mount(FuiField, { props: { modelValue: '', label: '账号', help: '本机服务可留空' } });
    expect(withHelp.find('.fui-field__help').text()).toBe('本机服务可留空');
    // and the hint must not leak into the DOM as a stray attribute, which is what it was before
    expect(withHelp.find('.fui-field').attributes('help')).toBeUndefined();

    const withoutHelp = mount(FuiField, { props: { modelValue: '', label: '账号' } });
    expect(withoutHelp.find('.fui-field__help').exists()).toBe(false);
  });

  it('keeps the non-model attributes where Vant expects them', () => {
    const wrapper = mount(FuiField, { props: { modelValue: '', label: '备注', type: 'textarea', rows: '2' } });
    // a textarea is a different element: if attrs landed on the wrapper this lookup finds nothing
    expect(wrapper.find('textarea').exists(), 'type="textarea" did not reach the Vant field').toBe(true);
  });
});
