import { describe, it, expect, beforeEach } from 'vitest';
import { useToastStore, pushToast } from './toast';

beforeEach(() => useToastStore.getState().clear());

describe('toast store (UI-REQ-002 surfacing)', () => {
  it('pushes a toast with a unique id', () => {
    const id = pushToast({ tone: 'error', title: 'boom' });
    expect(useToastStore.getState().toasts.map((t) => t.id)).toContain(id);
  });

  it('keeps at most 4 recent toasts (flood guard)', () => {
    for (let i = 0; i < 6; i++) pushToast({ tone: 'info', title: `t${i}` });
    expect(useToastStore.getState().toasts.length).toBe(4);
  });

  it('dismisses and clears', () => {
    const id = pushToast({ tone: 'success', title: 'ok' });
    useToastStore.getState().dismiss(id);
    expect(useToastStore.getState().toasts.find((t) => t.id === id)).toBeUndefined();
    pushToast({ tone: 'info', title: 'x' });
    useToastStore.getState().clear();
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
