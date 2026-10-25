import { describe, expect, it } from 'vitest';
import zh from '../i18n/locales/zh-CN';
import en from '../i18n/locales/en';
import { filesUnder, read } from './walk';

// UM-AC-21, as it now reads: the three M4 pages exist on mobile.
//
// This file used to assert the opposite (the MVP carve-out, docs/adr/004-m4-carve-out.md) and S1
// CP-UI-023 delivered the milestone since. The coverage is inverted rather than deleted, because the
// requirement is the same shape — a machine checks which pages this console offers — and because most
// of what is asserted here is *how* the three pages must be built: gated by allowed-actions, written
// through `FuiActionButton`, with a digest of the body in the idempotency intent, and no free-text
// field where S1 expects one of its own values.
//
// Checked the same three ways the absence test checked them: the route table, the source that
// implements it, and the vocabulary the API layer and both locales expose.

const ROUTER = 'src/router/index.ts';

const PAGES = [
  { name: 'project-members', path: '/projects/:id/members', view: 'src/features/project/ProjectMembersView.vue', titleKey: 'admin.membersTitle', action: 'ACTION.projectMemberManage', retry: 'members.refetch()', mutation: 'useReplaceMembers' },
  { name: 'project-grants', path: '/projects/:id/grants', view: 'src/features/project/ArtifactGrantsView.vue', titleKey: 'admin.grantsTitle', action: 'ACTION.artifactGrantManage', retry: 'grants.refetch()', mutation: 'useAddGrant' },
  { name: 'project-exemptions', path: '/projects/:id/exemptions', view: 'src/features/project/ProductionExemptionsView.vue', titleKey: 'admin.exemptionsTitle', action: 'ACTION.productionExemptionManage', retry: 'exemptions.refetch()', mutation: 'useAddExemption' },
];

/** A leaf lookup, so a title key is checked against the message object rather than against prose. */
function has(obj: unknown, path: string[]): boolean {
  return path.reduce<unknown>((acc, k) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[k] : undefined), obj) !== undefined;
}

/** The one source line of the route table that registers `name`, or null when there is none. */
function routeLine(name: string): string | null {
  return read(ROUTER).split(/\r?\n/).find((l) => l.includes(`name: '${name}'`)) ?? null;
}

describe('UM-AC-21 · M4 三页在手机端存在', () => {
  for (const page of PAGES) {
    it(`${page.name} is registered at ${page.path} with its own title and back arrow`, () => {
      const line = routeLine(page.name);
      expect(line, `${page.name} has no route entry`).not.toBeNull();
      expect(line).toContain(`path: '${page.path}'`);
      expect(line).toContain(`meta: { titleKey: '${page.titleKey}', showBack: true }`);
      // Lazy like every other route (UM-TECH-002), at the file that actually exists.
      expect(line).toContain(`component: () => import('@/features/project/`);
      expect(filesUnder('src/features/project', (p) => p.endsWith('.vue'))).toContain(page.view);
    });
  }

  for (const page of PAGES) {
    it(`${page.view} gates its writes on allowed-actions and renders its list through FuiAsync`, () => {
      const body = read(page.view);
      // UM-BR-01 / UM-BR-02: availability is S1's answer, never a local flag. A page that consults a
      // feature switch decides the question for itself — which is what the carve-out era did by
      // removing the routes instead.
      expect(body).toContain(page.action);
      expect(body).toContain(page.mutation);
      expect(body).toMatch(/:workflow-id="workflowId"/);
      expect(body).not.toMatch(/FEATURE_NOT_ENABLED|m4Enabled|VITE_[A-Z_]*FEATURE/);
      // Every write is a gated control: a bare FuiButton that mutates is the lapse the audit found.
      expect(body, `${page.view}: a write on an ungated FuiButton`).not.toMatch(/<FuiButton[\s>][\s\S]*?\.mutate\(/);
      expect(body).toMatch(/<FuiActionButton[\s>]/);
      // Loading / empty / error, and an error state that can be retried rather than a dead end.
      expect(body).toMatch(/<FuiAsync[^>]*:loading=/s);
      expect(body).toMatch(/<FuiAsync[^>]*:error=/s);
      expect(body).toMatch(/<FuiAsync[^>]*:empty=/s);
      expect(body).toMatch(/<FuiAsync[^>]*:empty-title=/s);
      expect(body).toContain(`@retry="${page.retry}"`);
    });
  }

  it('the API layer exports the M4 vocabulary the pages read', () => {
    for (const hook of ['useProjectMembers', 'useArtifactGrants', 'useProductionExemptions']) {
      expect(read('src/api/queries.ts'), hook).toMatch(new RegExp(`export const ${hook} = `));
    }
    for (const hook of ['useReplaceMembers', 'useRemoveMember', 'useAddGrant', 'useRevokeGrant', 'useAddExemption', 'useRevokeExemption']) {
      expect(read('src/api/mutations.ts'), hook).toMatch(new RegExp(`export function ${hook}\\(`));
    }
    for (const alias of ['MemberList', 'MemberView', 'MemberPutRequest', 'GrantList', 'GrantView', 'GrantCreateRequest', 'ExemptionList', 'ExemptionView', 'ExemptionCreateRequest']) {
      expect(read('src/api/types.ts'), alias).toMatch(new RegExp(`export type ${alias} = `));
    }
  });

  it('the requests reach S1 at the contract paths', () => {
    const queries = read('src/api/queries.ts');
    const mutations = read('src/api/mutations.ts');
    for (const route of ['members', 'artifact-grants', 'production-exemptions']) {
      expect(queries, route).toMatch(new RegExp(`/projects/\\$\\{encodeURIComponent\\(toValue\\(projectId\\)\\)\\}/${route}\``));
    }
    // Revoking an exemption is top-level in the contract (`/api/v1/production-exemptions/{id}`), not
    // under the project: a path built by analogy with the other two answers 404.
    expect(mutations).toMatch(/`\/production-exemptions\/\$\{encodeURIComponent\(v\.exemptionId\)\}`/);
    expect(mutations).toMatch(/`\/projects\/\$\{encodeURIComponent\(v\.projectId\)\}\/members\/\$\{encodeURIComponent\(v\.userId\)\}`/);
    // The grant revoke names the classification in the query, which is the route's required parameter.
    expect(mutations).toMatch(/\(v\) => \(\{ classification: v\.classification \}\)/);
  });

  it('every set-replacement and create intent carries a digest of the body', () => {
    // UI-REQ-001, and the reason desktop's `useReplaceMembers` changed: an intent keyed on the route
    // and the project alone makes a second edit of the same list look like a replay of the first, and
    // S1 answers with the *first* response.
    const mutations = read('src/api/mutations.ts');
    for (const route of ['members', 'artifact-grants', 'production-exemptions']) {
      expect(mutations, route).toMatch(new RegExp(`actionIntent\\('(PUT|POST)', '/projects/\\{project_id\\}/${route}', v\\.projectId, payloadDigest\\(JSON\\.stringify\\(v\\.body\\)\\)\\)`));
    }
    // The three revocations name their target in the path, and S1 reads no key on those routes.
    expect(mutations).not.toMatch(/actionIntent\('DELETE', '\/projects\/\{project_id\}\/(members|artifact-grants)/);
    expect(mutations).not.toMatch(/actionIntent\('DELETE', '\/production-exemptions/);
  });

  it('the exemption environment is chosen from GET /environments, never typed', () => {
    // The desktop original keeps a free-text environment field (S5 ProductionExemptions.tsx:107). A
    // waiver is matched on `environment_key`, so a key typed from memory is a waiver that never
    // applies and nothing on screen says why the gate still refuses.
    const view = read('src/features/project/ProductionExemptionsView.vue');
    expect(view).toMatch(/import \{[^}]*useEnvironments[^}]*\} from '@\/api\/queries'/);
    expect(view).not.toMatch(/v-model="draft\.environmentKey"/);
    expect(view).toMatch(/draft\.environmentKey = e\.environmentKey/);
  });

  it('both locales carry the M4 labels and every route title key resolves', () => {
    for (const [name, bundle] of [['zh-CN', zh], ['en', en]] as const) {
      for (const key of ['membersTitle', 'grantsTitle', 'exemptionsTitle', 'addMember', 'remove', 'revoke', 'classification', 'waiver', 'environment']) {
        expect(has(bundle, ['admin', key]), `${name}: admin.${key}`).toBe(true);
      }
    }
    // A key present in one language only is what `i18n/parity.test.ts` catches; this catches a route
    // pointing at a key that exists in neither.
    for (const page of PAGES) {
      expect(has(zh, page.titleKey.split('.')), page.titleKey).toBe(true);
      expect(has(en, page.titleKey.split('.')), page.titleKey).toBe(true);
    }
  });

  it('the project it belongs to links each of the three pages', () => {
    // Absence used to be the point, so nothing linked to them. A route no screen can reach is only a
    // deep link, and this criterion is about the pages being offered.
    const detail = read('src/features/projects/ProjectDetailView.vue');
    // Vitest's `expect` takes no message argument, so name the offenders in the value under test:
    // a failure prints the paths that have no tile rather than an opaque string diff.
    const unlinked = PAGES.filter(
      (page) => !detail.includes(`/projects/\${projectId.value}/${page.path.split('/').pop()}`),
    ).map((page) => page.path);
    expect(unlinked).toEqual([]);
  });

  it('the mock harness answers all six routes and gates nothing on M4', () => {
    // The e2e specs run against this file: a page that renders against a harness answering every new
    // route with an empty envelope would prove nothing about its write paths.
    const mock = read('scripts/mock-s1.mjs');
    for (const route of ['members', 'artifact-grants', 'production-exemptions']) {
      expect(mock, route).toContain('`GET /projects/${P}/' + route + '`');
    }
    expect(mock).toMatch(/path === `\/projects\/\$\{P\}\/members` && req\.method === 'PUT'/);
    // The two revocations are addressed by a path pattern, exactly as the contract routes them.
    expect(mock).toContain(String.raw`\/artifact-grants\/([^/]+)$`);
    expect(mock).toContain(String.raw`^\/production-exemptions\/([^/]+)$`);
    // CP-UI-023's milestone gate is gone from the fixture: the three project rights answer as allowed.
    for (const action of ['member.manage', 'restricted_grant.manage', 'production_exemption.manage']) {
      expect(mock, action).toContain(`{ action: '${action}', available: true }`);
      expect(mock, action).not.toMatch(new RegExp(`action: '${action.replace('.', '\\.')}'[^\\n]*FEATURE_NOT_ENABLED`));
    }
  });

  it('the access screen still keeps only session, users and roles', () => {
    // The three pages are project-scoped, so they are project routes — not a sixth, seventh and
    // eighth tab on /access. That half of the old absence test still holds.
    const view = read('src/features/access/AccessView.vue');
    for (const tab of ["value: 'session'", "value: 'users'", "value: 'roles'"]) expect(view).toContain(tab);
    for (const tab of ["value: 'members'", "value: 'grants'", "value: 'exemptions'"]) expect(view).not.toContain(tab);
  });
});
