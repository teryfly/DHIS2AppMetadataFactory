import type { SetupView } from '@/api/types';
import { capabilityEntry, capabilityLabel } from '@/lib/labels';

/**
 * The guided checklist's branch table (UM-SET-001 … 005), kept apart from the view so the mapping
 * from S1's facts to a step's state is testable without a browser.
 *
 * Facts come from `GET /setup` only; whether a control may be used keeps coming from
 * `allowed-actions` (UM-BR-01). A step whose facts S1 withheld is `pending` with the "not visible to
 * you" note — never `pass`, and never a zero read as "nothing registered" (UM-BR-02).
 */
export type StartState = 'pass' | 'fail' | 'pending';

export interface StartStep {
  key: string;
  state: StartState;
  /** i18n key of the fact line, rendered with `params`. */
  noteKey: string;
  params: Record<string, string | number>;
  /** Route the step sends the operator to. */
  to: string;
  /**
   * The facts behind this step were withheld from the caller, as opposed to merely unfinished.
   *
   * The distinction decides whether the step counts as work left to do. `pending` alone cannot
   * carry it, because it also means "partly done", and a section this operator has no right to read
   * is not a task they can complete — counting it would tell them to go and do something they
   * cannot. The desktop table has a fourth state for this (`unknown`); the checklist renders three
   * labels, so the flag lives beside the state instead.
   */
  withheld: boolean;
}

/**
 * Whether the deployment's three capability ports answer with the real thing.
 *
 * This is a fact about the server's own configuration (`provider_mode` in the env file), so it is
 * reported on the guide rather than listed as a step: no control in a browser can complete it, and a
 * checklist item nobody can tick teaches operators to ignore the checklist.
 */
export function capabilityFacts(facts: SetupView | undefined): { ready: boolean; mode: string; which: string } {
  const providers = facts?.providers ?? {};
  const faked = Object.entries(providers).filter(([, mode]) => mode !== 'real');
  return {
    ready: faked.length === 0,
    // The server names its ports `S2`/`S3`/`S4` and reports `real`/`fake`; those tokens stay in the
    // API. A label this console has never heard of is passed through rather than dropped.
    mode: Object.entries(providers).map(([name, mode]) => capabilityEntry(name, mode)).join(' · '),
    which: faked.map(([name]) => capabilityLabel(name)).join(', '),
  };
}

export function startSteps(facts: SetupView | undefined, create: { available: boolean; reason: string }): StartStep[] {
  if (!facts) return [];
  const environments = facts.environments;
  const intelligence = facts.intelligence ?? null;
  const totalEnvs = environments?.total ?? 0;
  const untested = environments?.neverTested ?? [];
  const nonProd = environments?.nonProductionInService ?? 0;
  const projects = facts.projects?.total ?? 0;
  const activeProjects = facts.projects?.active ?? 0;

  return [
    {
      key: 'models',
      state:
        intelligence === null
          ? 'pending'
          : (intelligence.modelsEnabled ?? 0) > 0
            ? 'pass'
            : (intelligence.models ?? 0) > 0
              ? 'pending'
              : 'fail',
      noteKey:
        intelligence === null
          ? 'start.modelsHidden'
          : (intelligence.modelsEnabled ?? 0) > 0
            ? 'start.modelsReady'
            : 'start.modelsMissing',
      params: {
        providers: intelligence?.providers ?? 0,
        models: intelligence?.models ?? 0,
        enabled: intelligence?.modelsEnabled ?? 0,
      },
      to: '/governance/models',
      withheld: intelligence === null,
    },
    {
      key: 'environments',
      state: totalEnvs === 0 ? 'fail' : untested.length > 0 || nonProd === 0 ? 'pending' : 'pass',
      noteKey:
        totalEnvs === 0
          ? 'start.environmentsMissing'
          : untested.length > 0
            ? 'start.environmentsUntested'
            : nonProd === 0
              ? 'start.environmentsNoNonProd'
              : 'start.environmentsReady',
      params: {
        total: totalEnvs,
        inService: environments?.inService ?? 0,
        untested: untested.join('、') || '—',
      },
      to: '/environments',
      withheld: false,
    },
    {
      key: 'project',
      // 项目存在是一个事实，与 create 权限无关；被拒只解释「为什么还没有」。
      state: projects > 0 ? 'pass' : create.available ? 'fail' : 'pending',
      noteKey:
        projects > 0
          ? 'start.projectsReady'
          : create.available
            ? 'start.projectsMissing'
            : 'start.projectsDenied',
      params: { total: projects, active: activeProjects, reason: create.reason },
      to: projects > 0 ? '/projects' : '/projects/new',
      // an existing project is a fact whoever may see it; only "nothing yet, and you may not
      // create one" is the withheld case
      withheld: projects === 0 && !create.available,
    },
    {
      key: 'pipeline',
      state: activeProjects > 0 ? 'pending' : 'fail',
      noteKey: activeProjects > 0 ? 'start.pipelineNext' : 'start.pipelineWaiting',
      params: {},
      to: '/projects',
      withheld: false,
    },
  ];
}

/**
 * Steps that still stop a first run.
 *
 * `pipeline` is the project's own work rather than a setup step, and a withheld section is not
 * something this operator can act on — the desktop summary excludes both, and the two dashboards
 * have to count the same deployment the same way or one of them is wrong by construction.
 */
export function blockingSteps(steps: StartStep[]): StartStep[] {
  return steps.filter((step) => step.key !== 'pipeline' && !step.withheld && (step.state === 'fail' || step.state === 'pending'));
}
