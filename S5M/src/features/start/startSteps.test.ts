/*
 * UM-SET-001 … 005. The view is a renderer; the requirement lives in this mapping from S1's facts
 * to a step's state, so it is tested as a function. The failure modes worth pinning are the ones
 * that would tell an operator something untrue: "ready" while nothing is registered, or a zero
 * where the server said "you may not see this".
 */
import { describe, it, expect } from 'vitest';
import { capabilityFacts, startSteps, blockingSteps } from './startSteps';
import { i18n } from '@/i18n';
import type { SetupView } from '@/api/types';

const OPEN = { available: true, reason: '' };

function facts(patch: Partial<SetupView> = {}): SetupView {
  return {
    generatedAt: '2026-10-03T00:00:00Z',
    providers: { S2: 'real', S3: 'real', S4: 'real' },
    configuration: { providerMode: 'auto', workflowEngine: 'inprocess', migrationsMode: 'apply', httpsOnly: true, authMode: 'off', problems: [] },
    identity: { authMode: 'off', enforced: false, m4Enabled: false, bootstrapAdminDeclared: false, users: 0, pendingPasswordChange: false },
    environments: { total: 0, inService: 0, nonProductionInService: 0, neverTested: [], items: [] },
    intelligence: { providers: 1, providersEnabled: 1, models: 2, modelsEnabled: 1 },
    projects: { total: 0, active: 0 },
    ...patch,
  } as SetupView;
}

const stateOf = (key: string, input: SetupView, create = OPEN) =>
  startSteps(input, create).find((step) => step.key === key)?.state;

describe('the guided checklist’s branch table', () => {
  it('calls a bare install "not configured" rather than empty', () => {
    const bare = facts();
    expect(stateOf('environments', bare)).toBe('fail');
    expect(stateOf('project', bare)).toBe('fail');
    expect(blockingSteps(startSteps(bare, OPEN)).map((s) => s.key)).toContain('environments');
  });

  it('does not count a stand-in capability as wired up', () => {
    i18n.global.locale.value = 'en';
    // reported as a fact, not as a step: `provider_mode` is chosen in the server's env file, so the
    // checklist has nothing for an operator to complete here
    const seen = capabilityFacts(facts({ providers: { S2: 'real', S3: 'fake', S4: 'real' } }));
    expect(seen.ready).toBe(false);
    expect(seen.which).toBe('Requirement parsing');
    expect(seen.mode).toBe('Blueprint compilation: live · Requirement parsing: demo stand-in · Environment execution: live');
  });

  it('keeps a label it has never seen readable, because the contract does not bound this map', () => {
    i18n.global.locale.value = 'en';
    // `providers` is `{[key: string]: string}`; a deployment can arrive with a port name or a mode
    // this console has never heard of. Blank would read as "no capability" instead of "unrecognised".
    const seen = capabilityFacts(facts({ providers: { 'openai-compatible': 'weird' } }));
    expect(seen.mode).toBe('openai-compatible: weird');
    expect(seen.which).toBe('openai-compatible');
  });

  it('keeps an untested environment out of "ready", because the validation stage would refuse it', () => {
    const step = startSteps(
      facts({ environments: { total: 2, inService: 2, nonProductionInService: 2, neverTested: ['stage'], items: [] } }),
      OPEN,
    ).find((s) => s.key === 'environments')!;
    expect(step.state).toBe('pending');
    expect(step.noteKey).toBe('start.environmentsUntested');
  });

  it('names production-only service as blocking a first project', () => {
    const step = startSteps(
      facts({ environments: { total: 1, inService: 1, nonProductionInService: 0, neverTested: [], items: [] } }),
      OPEN,
    ).find((s) => s.key === 'environments')!;
    expect(step.noteKey).toBe('start.environmentsNoNonProd');
  });

  it('shows a withheld governance section as not visible, never as zero models', () => {
    const step = startSteps(facts({ intelligence: null }), OPEN).find((s) => s.key === 'models')!;
    expect(step.state).toBe('pending');
    expect(step.noteKey).toBe('start.modelsHidden');
  });

  it('reports a refused create through the server’s reason', () => {
    const step = startSteps(facts({ projects: { total: 0, active: 0 } }), {
      available: false,
      reason: '你没有该权限',
    }).find((s) => s.key === 'project')!;
    expect(step.state).toBe('pending');
    expect(step.noteKey).toBe('start.projectsDenied');
    expect(step.params.reason).toBe('你没有该权限');
  });

  it('treats every step as done on a configured machine', () => {
    const ready = facts({
      environments: { total: 2, inService: 2, nonProductionInService: 1, neverTested: [], items: [] },
      projects: { total: 1, active: 1 },
    });
    const blocking = blockingSteps(startSteps(ready, OPEN));
    // `pipeline` is deliberately excluded from `blockingSteps`: running a stage is the project's own
    // work, not a configuration gap, and nagging about it would bury the real gaps.
    expect(blocking).toEqual([]);
  });

  it('counts an existing project as existing even when create is refused', () => {
    // availability answers what may still be done; it does not rewrite what has been done
    const step = startSteps(facts({ projects: { total: 2, active: 1 } }), {
      available: false,
      reason: '你没有该权限',
    }).find((s) => s.key === 'project')!;
    expect(step.state).toBe('pass');
    expect(step.noteKey).toBe('start.projectsReady');
    expect(step.to).toBe('/projects');
  });
});
