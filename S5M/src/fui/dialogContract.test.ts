import { describe, expect, it } from 'vitest';
import { h } from 'vue';
import { mount } from '@vue/test-utils';
import { Dialog as VanDialog } from 'vant';
import FuiDialog from '@/fui/FuiDialog.vue';

// UM-AUTH-001: a sign-in dialog has to survive a confirm that did not sign anybody in. Vant's Dialog
// closes on every action and has no `close-on-confirm` prop, so `beforeClose` is the only hook, and
// it is a *declared* prop here rather than a fall-through attribute because the dialog teleports its
// root — an unknown attribute would land on the wrapper and be silently dropped.
//
// What is asserted is the one thing this wrapper owns: that the interceptor reaches Vant. Whether the
// dialog then stays open is Vant's behaviour and is covered in the browser (tests/e2e/sessionLapse.spec.ts),
// where a jsdom click on a teleported Vant footer never reaches the handler.
describe('FuiDialog beforeClose', () => {
  it('passes the interceptor through to the Vant dialog', () => {
    const beforeClose = (action: string) => action !== 'confirm';
    const wrapper = mount({ render: () => h(FuiDialog, { show: true, beforeClose }) });
    const dialog = wrapper.findComponent(VanDialog);
    expect(dialog.exists()).toBe(true);
    expect(dialog.props('beforeClose')).toBe(beforeClose);
    wrapper.unmount();
  });

  it('leaves Vant to its own default when no interceptor is given', () => {
    const wrapper = mount({ render: () => h(FuiDialog, { show: true }) });
    expect(wrapper.findComponent(VanDialog).props('beforeClose')).toBeFalsy();
    wrapper.unmount();
  });
});
