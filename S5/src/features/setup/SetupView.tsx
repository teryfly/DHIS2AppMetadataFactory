import type { SetupView as SetupFacts } from '@/api/types';
import { capabilityEntry, capabilityLabel } from '@/lib/labels';

/**
 * Getting-started checklist (UI-SET-001 … 005).
 *
 * Every state here is derived from two sources and nothing else: the facts of `GET /setup`
 * (S5IF-SET01) and `GET /allowed-actions`. The UI never infers a business state of its own
 * (UI-BR-02) — which is why a section S1 withholds renders as "not visible to you" instead of as
 * zero rows, and why a step whose action S1 did not list says "not available to you" rather than
 * being hidden (UI-BR-01).
 */

export type StepState = 'ready' | 'partial' | 'missing' | 'unknown';

export interface SetupStep {
  id: string;
  state: StepState;
  /** Localised fact line — what the server actually said. */
  fact: { key: string; options?: Record<string, string | number> };
  /** Localised consequence line — what happens while it stays this way. */
  consequence: { key: string; options?: Record<string, string | number> };
  to: string;
}

export const STATE_SKIN: Record<StepState, 'neutral' | 'positive' | 'negative' | 'default'> = {
  ready: 'positive',
  partial: 'neutral',
  missing: 'negative',
  unknown: 'default',
};

/**
 * Whether the deployment's three capability ports answer with the real thing.
 *
 * A fact about the server's own configuration (`provider_mode` in the env file), so the guide reports
 * it instead of listing it: no control in a browser can complete it, and a checklist item nobody can
 * tick teaches operators to ignore the checklist. `providers` arrives as the server's own port labels
 * and `real`/`fake`; the operator reads capability names, and an unknown label stays as sent rather
 * than vanishing.
 */
export function capabilityFacts(facts: SetupFacts | undefined): { ready: boolean; mode: string; which: string } {
  const providers = facts?.providers ?? {};
  const faked = Object.entries(providers).filter(([, mode]) => mode !== 'real');
  return {
    ready: faked.length === 0,
    mode: Object.entries(providers).map(([k, v]) => capabilityEntry(k, v)).join(' · '),
    which: faked.map(([k]) => capabilityLabel(k)).join(', '),
  };
}

/** Pure facts → steps mapping, exported so the branch table is testable without a DOM. */
export function setupSteps(
  facts: SetupFacts | undefined,
  create: { available: boolean; reason: string },
): SetupStep[] {
  if (!facts) return [];
  const environments = facts.environments;
  const intelligence = facts.intelligence;

  return [
    {
      id: 'models',
      state:
        intelligence == null
          ? 'unknown'
          : (intelligence.modelsEnabled ?? 0) > 0
            ? 'ready'
            : (intelligence.models ?? 0) > 0
              ? 'partial'
              : 'missing',
      fact:
        intelligence == null
          ? { key: 'setup.modelsHidden' }
          : {
              key: 'setup.modelsFact',
              options: { providers: intelligence.providers, models: intelligence.models, enabled: intelligence.modelsEnabled },
            },
      consequence: {
        key:
          intelligence == null
            ? 'setup.modelsNoRights'
            : (intelligence.modelsEnabled ?? 0) > 0
              ? 'setup.modelsReady'
              : 'setup.modelsMissing',
      },
      to: '/governance/models',
    },
    {
      id: 'environments',
      state:
        (environments?.total ?? 0) === 0
          ? 'missing'
          : (environments?.neverTested?.length ?? 0) > 0 || (environments?.nonProductionInService ?? 0) === 0
            ? 'partial'
            : 'ready',
      fact: {
        key: 'setup.environmentsFact',
        options: {
          total: environments?.total ?? 0,
          inService: environments?.inService ?? 0,
          untested: (environments?.neverTested ?? []).join(', ') || '—',
        },
      },
      consequence: {
        key:
          (environments?.total ?? 0) === 0
            ? 'setup.environmentsMissing'
            : (environments?.neverTested?.length ?? 0) > 0
              ? 'setup.environmentsUntested'
              : (environments?.nonProductionInService ?? 0) === 0
                ? 'setup.environmentsNoNonProd'
                : 'setup.environmentsReady',
      },
      to: '/environments',
    },
    {
      id: 'project',
      // A project that exists is a fact, whatever the create permission says; the refusal only
      // explains why there is nothing to show. (UI-BR-01 governs availability, not what happened.)
      state: (facts.projects?.total ?? 0) > 0 ? 'ready' : create.available ? 'missing' : 'unknown',
      fact: { key: 'setup.projectsFact', options: { total: facts.projects?.total ?? 0, active: facts.projects?.active ?? 0 } },
      consequence:
        (facts.projects?.total ?? 0) > 0
          ? { key: 'setup.projectsReady' }
          : create.available
            ? { key: 'setup.projectsMissing' }
            : // S1's own reason (or "S1 listed no entry"), never a guess about why (UI-BR-01).
              { key: 'setup.projectsDenied', options: { reason: create.reason } },
      to: (facts.projects?.total ?? 0) > 0 ? '/projects' : '/projects/new',
    },
    {
      id: 'pipeline',
      state: (facts.projects?.active ?? 0) > 0 ? 'partial' : 'missing',
      fact: { key: 'setup.pipelineFact' },
      consequence: {
        key: (facts.projects?.active ?? 0) > 0 ? 'setup.pipelineNext' : 'setup.pipelineWaiting',
      },
      to: '/projects',
    },
  ];
}
