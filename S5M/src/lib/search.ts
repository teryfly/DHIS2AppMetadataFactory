/**
 * UM-NAV-004: the top search reaches four kinds of thing — 项目名、阶段、环境键、businessKey — and a
 * hit has to be jumpable, not just listed.
 *
 * The reason this is a module and not a computed in the shell: the four sources have different
 * scopes and different failure modes, and the property worth pinning is which group a term lands in
 * and where it sends you. Against S1 the sources are what their endpoints can answer — projects and
 * environments are list-wide (`name` filters server-side), while stages and registry objects only
 * exist per project, so those two groups search the project the operator was last in. The shell says
 * so on screen rather than letting a stage search look global.
 */

export type SearchKind = 'project' | 'stage' | 'environment' | 'object';

export interface SearchItem {
  kind: SearchKind;
  id: string;
  label: string;
  /** Where the hit takes the operator. */
  to: string;
  /** A second line: the class of an environment, the type of a registry object. */
  sub?: string;
}

export interface SearchGroup {
  kind: SearchKind;
  items: SearchItem[];
}

/** Nullable on the wire — S1's views answer these fields with `null` as often as with a string. */
type Text = string | null | undefined;

interface ProjectRow { id?: Text; name?: Text }
interface StageRow { id?: Text; stageType?: Text; status?: Text }
interface EnvironmentRow { environmentKey?: Text; name?: Text; environmentClass?: Text }
interface RegistryRow { businessKey?: Text; dhis2Type?: Text }

export interface SearchSource {
  projects: ProjectRow[];
  stages: StageRow[];
  environments: EnvironmentRow[];
  objects: RegistryRow[];
  /** The project the stage and object groups were read from; empty when there is none. */
  projectId: string;
}

const MAX_PER_GROUP = 6;

function matches(haystack: Text[], term: string): boolean {
  const needle = term.toLowerCase();
  return haystack.some((value) => String(value ?? '').toLowerCase().includes(needle));
}

function cap(items: SearchItem[]): SearchItem[] {
  return items.slice(0, MAX_PER_GROUP);
}

/**
 * One term, four groups. An empty term asks nothing and returns nothing: the panel shows the
 * operator's recent objects in that case, and mixing "everything" into it would hide them.
 */
export function searchAll(term: string, source: SearchSource): SearchGroup[] {
  const q = term.trim();
  if (!q) return [];

  const groups: SearchGroup[] = [];

  const projects = cap(
    source.projects
      .filter((p) => matches([p.name, p.id], q))
      // No second line on purpose: the only thing worth adding is "archived", and that is a word
      // this module cannot translate — a raw token here would put English prose on a Chinese screen.
      .map((p) => ({ kind: 'project' as const, id: String(p.id), label: String(p.name ?? p.id), to: `/projects/${encodeURIComponent(String(p.id))}` })),
  );
  if (projects.length) groups.push({ kind: 'project', items: projects });

  const stages = cap(
    source.stages
      .filter((s) => matches([s.stageType, s.id], q))
      .map((s) => ({ kind: 'stage' as const, id: String(s.id), label: String(s.stageType ?? s.id), to: `/stages/${encodeURIComponent(String(s.id))}`, sub: s.status ? String(s.status) : undefined })),
  );
  if (stages.length) groups.push({ kind: 'stage', items: stages });

  const environments = cap(
    source.environments
      .filter((e) => matches([e.environmentKey, e.name, e.environmentClass], q))
      .map((e) => ({ kind: 'environment' as const, id: String(e.environmentKey), label: String(e.environmentKey ?? e.name), to: `/environments/${encodeURIComponent(String(e.environmentKey))}`, sub: e.environmentClass ? String(e.environmentClass) : undefined })),
  );
  if (environments.length) groups.push({ kind: 'environment', items: environments });

  const objects = cap(
    source.objects
      .filter((o) => matches([o.businessKey, o.dhis2Type], q))
      .map((o) => ({
        kind: 'object' as const,
        id: String(o.businessKey),
        label: String(o.businessKey),
        // The graph page is where a businessKey lives; `?businessKey=` preselects the node, so the
        // jump lands on the object rather than on a canvas the operator then has to search by hand.
        to: `/projects/${encodeURIComponent(source.projectId)}/graph?businessKey=${encodeURIComponent(String(o.businessKey))}`,
        sub: o.dhis2Type ? String(o.dhis2Type) : undefined,
      })),
  );
  if (objects.length) groups.push({ kind: 'object', items: objects });

  return groups;
}
