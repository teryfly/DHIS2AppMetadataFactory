/*
 * The transient progress store (UM-STG-004). The one field this suite exists for is `runId`:
 * the cancel-agent-run button has no other way to address the run, so a frame that omits the id
 * must never erase the id an earlier frame carried, and out-of-order frames must not roll it back.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useProgressStore } from './progress';

describe('progress store (UM-STG-004)', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('keeps the run id from the agent frame so cancel can address the run', () => {
    const s = useProgressStore();
    s.update('WF-1', 'agent', 'ST-1', 5, { currentStep: 'mapping', runId: 'AR-9' });
    expect(s.get('WF-1', 'agent', 'ST-1')?.runId).toBe('AR-9');

    // A later frame without the id updates the step but does not erase the address.
    s.update('WF-1', 'agent', 'ST-1', 6, { currentStep: 'patching' });
    const e = s.get('WF-1', 'agent', 'ST-1');
    expect(e?.currentStep).toBe('patching');
    expect(e?.runId).toBe('AR-9');
  });

  it('drops out-of-order frames, run id included', () => {
    const s = useProgressStore();
    s.update('WF-1', 'agent', 'ST-1', 7, { currentStep: 'newer', runId: 'AR-7' });
    s.update('WF-1', 'agent', 'ST-1', 6, { currentStep: 'older', runId: 'AR-6' });
    const e = s.get('WF-1', 'agent', 'ST-1');
    expect(e?.currentStep).toBe('newer');
    expect(e?.runId).toBe('AR-7');
  });

  it('clears per workflow when the stage leaves RUNNING', () => {
    const s = useProgressStore();
    s.update('WF-1', 'agent', 'ST-1', 1, { runId: 'AR-1' });
    s.update('WF-2', 'agent', 'ST-9', 1, { runId: 'AR-9' });
    s.clearWorkflow('WF-1');
    expect(s.get('WF-1', 'agent', 'ST-1')).toBeUndefined();
    expect(s.get('WF-2', 'agent', 'ST-9')?.runId).toBe('AR-9');
  });
});
