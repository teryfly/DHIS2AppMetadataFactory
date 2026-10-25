/*
 * Zustand holds ONLY UI/browser state (UI-APP-006): operator name, language, layout, filters,
 * connection status and ephemeral progress. None of these store business state.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useOperatorStore, getOperatorName, hasOperator } from './operator';
import { useLanguageStore, getAcceptLanguage } from './language';
import { useUiStore } from './ui';
import { useConnectionStore } from './connection';
import { useProgressStore } from '@/sse/progress';

beforeEach(() => {
  useOperatorStore.setState({ name: null });
  useUiStore.setState({ selectedObjectKey: null, inspectorOpen: true, filters: {}, operatorDialogOpen: false });
});

describe('operator store (UI-OP)', () => {
  it('trims to null when empty so gating treats it as unset', () => {
    useOperatorStore.getState().setName('   ');
    expect(hasOperator()).toBe(false);
    expect(getOperatorName()).toBeNull();
    useOperatorStore.getState().setName('  Ana  ');
    expect(getOperatorName()).toBe('Ana');
    expect(hasOperator()).toBe(true);
  });
  it('clear removes the name', () => {
    useOperatorStore.getState().setName('Bob');
    useOperatorStore.getState().clear();
    expect(useOperatorStore.getState().name).toBeNull();
  });
});

describe('language store (UI-APP-002)', () => {
  it('getAcceptLanguage reflects the current language', () => {
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(getAcceptLanguage()).toBe('zh-CN');
    useLanguageStore.getState().setLanguage('en');
    expect(getAcceptLanguage()).toBe('en');
  });
});

describe('ui store (UI-APP-006 layout only)', () => {
  it('selection, filters and dialog are UI-only', () => {
    useUiStore.getState().selectObject('entity:facility');
    expect(useUiStore.getState().selectedObjectKey).toBe('entity:facility');
    useUiStore.getState().setFilter('q', 'abc');
    expect(useUiStore.getState().filters.q).toBe('abc');
    useUiStore.getState().clearFilters();
    expect(useUiStore.getState().filters).toEqual({});
    useUiStore.getState().setViewMode('REQUIREMENTS', 'graph');
    expect(useUiStore.getState().viewModeByStage.REQUIREMENTS).toBe('graph');
    useUiStore.getState().toggleInspector();
    expect(useUiStore.getState().inspectorOpen).toBe(false);
    useUiStore.getState().openOperatorDialog();
    expect(useUiStore.getState().operatorDialogOpen).toBe(true);
    useUiStore.getState().closeOperatorDialog();
    expect(useUiStore.getState().operatorDialogOpen).toBe(false);
  });
});

describe('connection store (UI-RT-005)', () => {
  it('tracks transport status', () => {
    useConnectionStore.getState().setStatus('offline');
    expect(useConnectionStore.getState().status).toBe('offline');
    useConnectionStore.getState().setStatus('open');
    expect(useConnectionStore.getState().status).toBe('open');
  });
});

describe('progress store (UI-RT-004, transient display only)', () => {
  it('ignores out-of-order frames by monotonic sequence', () => {
    useProgressStore.setState({ byWorkflow: {} });
    const up = useProgressStore.getState().upsert;
    up('k', { workflowId: 'WF', lastSeq: 10, currentStep: 'a' });
    up('k', { workflowId: 'WF', lastSeq: 5, currentStep: 'stale' }); // older → ignored
    expect(useProgressStore.getState().byWorkflow.k?.currentStep).toBe('a');
    up('k', { workflowId: 'WF', lastSeq: 11, currentStep: 'b' });
    expect(useProgressStore.getState().byWorkflow.k?.currentStep).toBe('b');
    useProgressStore.getState().clear('k');
    expect(useProgressStore.getState().byWorkflow.k).toBeUndefined();
  });
});
