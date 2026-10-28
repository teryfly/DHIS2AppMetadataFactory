// Hermetic S1 stand-in for the e2e suite (UM-NFR-07: "S1 OpenAPI Mock 服务，含 SSE 不可用场景").
// Dependency-free so it runs anywhere Node runs, and so the test substrate is reviewable in one
// file rather than spread over a library's fixtures.
//
// Modes, via env:
//   MOCK_SSE=off      SSE endpoint returns 503 (UM-AC-07 / UM-NET-004 polling fallback)
//   MOCK_UNCOMPILED=1 derived resources answer 404 NOT_FOUND without details.reason, i.e. exactly
//                     what S1 does today, so UM-AC-19 is testable before CP-UI-022 lands.
import { createServer } from 'node:http';

const PORT = Number(process.env.MOCK_S1_PORT ?? 8787);
let sseOff = process.env.MOCK_SSE === 'off';
// Runtime-togglable so one mock instance can serve both the "compiled" and the "not generated
// yet" arms of UM-AC-19; `GET /_control?mode=…` is a test-only lever, not part of the contract.
let uncompiled = process.env.MOCK_UNCOMPILED === '1';
// `bare` is the other cold-start lever: nothing registered at all, which is the state a fresh
// install actually boots into. The guided checklist and the wizard's environment step are only
// testable against it, because with fixtures loaded every prerequisite already looks satisfied.
let bare = false;
// Auth lever for UM-NET-009. The harness answered `authMode: off` unconditionally, which is the one
// state in which the app has no sign-in panel to show — the lapse behaviour therefore could not be
// exercised here at all. Off by default: every other spec keeps running against `off` exactly as
// before, and only a spec that asks for `/_control?mode=auth-on` gets an account deployment.
let authOn = process.env.MOCK_AUTH === 'local';
// Single sign-on (UM-AUTH-001). A deployment that offers the organisation's door reports
// `authMode: both`, and the sign-in screen only shows that button when it does — so without this
// lever the whole SSO half of the screen is untestable here. The state is deliberately fixed: the
// browser cannot observe the redirect to the IdP, so a spec drives the return trip itself with a
// stubbed 302 and needs to know which `state` to carry back. The exchange still refuses when the
// echo is wrong, which is the part worth testing.
let oidcOn = false;
const OIDC_STATE = 'mock-state';
const OIDC = {
  authorizationEndpoint: 'http://127.0.0.1:8787/fake-idp/authorize',
  clientId: 'mock-client',
  nonce: 'mock-nonce',
  responseType: 'code',
  scope: 'openid profile email',
};
// A lapse is different from "never signed in": the session existed and expired, so reads refuse with
// 401 while `/auth/session` answers 200 + `authenticated: false`. And a session only exists because
// somebody signed in — reporting `authenticated: true` merely because the deployment is an account
// one is how the harness ended up handing out a login nobody asked for.
let signedInNow = false;
let lapsed = false;
let passwordChangePending = false;
// The `lock-user` lever for the users list (see `GET /users` below).
let lockedRow = false;
// Accounts the app has created through `POST /users` during this run. Kept in the harness rather
// than echoed by the test so the list re-read shows what S1 would show: the login name *and* the
// display name, from the server, after the write.
const createdUsers = [];
// The two rows the `lock-user` lever puts in the list. Mutable, because the account screen now also
// edits and retires accounts: a PATCH that answered correctly but listed the old values would let a
// test pass on a write the console never shows back.
const LOCK_ROWS = [
  { id: 'usr-ada', subject: 'ada@example.org', displayName: 'Ada', email: '', source: 'local', status: 'ACTIVE', locked: false, mustChangePassword: false, roles: [{ role: 'SOLUTION_ARCHITECT', projectId: null, revoked: null }] },
  { id: 'usr-larry', subject: 'larry@example.org', displayName: 'Larry', email: '', source: 'local', status: 'ACTIVE', locked: true, mustChangePassword: false, roles: [] },
];
const LOCK_DEFAULTS = JSON.parse(JSON.stringify(LOCK_ROWS));
const resetLockRows = () => LOCK_ROWS.splice(0, LOCK_ROWS.length, ...JSON.parse(JSON.stringify(LOCK_DEFAULTS)));
const ACCOUNT = { subject: 'tester@example.org', displayName: '测试员', userId: 'usr-mock-tester', password: 'correct-horse' };

/** What `GET /auth/session` and a successful sign-in answer, in S1's own `SessionView` shape. */
function sessionView() {
  const mode = !authOn ? 'off' : oidcOn ? 'both' : 'local';
  if (!signedInNow || lapsed) return { authenticated: false, authMode: mode };
  return {
    authenticated: true,
    authMode: mode,
    userId: ACCOUNT.userId,
    subject: ACCOUNT.subject,
    displayName: ACCOUNT.displayName,
    roles: ['PLATFORM_ADMINISTRATOR'],
    csrfToken: 'mock-csrf-1',
    expiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
    mustChangePassword: passwordChangePending,
  };
}

const P = 'proj-1';
const W = 'wf-1';
const STAGE_COMPILE = 'stage-baseline-compile';

// The project row is live because PATCH /projects/{id} edits it from the settings screen.
const projectRow = {
  id: P,
  name: '免疫规划试点',
  key: 'epi',
  ownerName: 'Ana',
  description: 'mock',
  projectType: 'GREENFIELD',
  versionConstraints: '2.41.x',
  defaultLanguage: 'zh-CN',
  translationLanguages: ['en'],
  status: 'ACTIVE',
  deliveryMode: 'B',
  workflowId: W,
  workflowStatus: 'IN_PROGRESS',
  currentStageId: STAGE_COMPILE,
  archived: false,
  currentBlueprintVersion: '1.0.0',
  targetEnvironments: [{ environmentKey: 'dev', name: 'DEV', environmentClass: 'TEST', promotionOrder: 1, isDesignReference: false }],
};

const envelope = (items, extra = {}) => ({ items, limit: items.length, total: items.length, offset: 0, ...extra });

// One questionnaire for the review page: a recommended single-choice question and a
// multi-choice one, so "accept all recommendations" has both paths to exercise.
const mockQuestions = [
  {
    id: 'q-1',
    workflowId: W,
    projectId: P,
    stageId: STAGE_COMPILE,
    text: '年龄字段是否包含边界？',
    severity: 'HIGH',
    answerMode: 'SINGLE',
    blocking: true,
    status: 'OPEN',
    affects: ['dataElement:age'],
    suggestions: [
      { id: 's1', text: '包含边界（沿用现有数据元）', rationale: '与现有 DHIS2 数据元口径一致，不新增对象。', recommended: true, changesUid: false, regenerates: [] },
      { id: 's2', text: '不包含边界（新建数据元）', rationale: '会新增数据元并改变校验规则。', recommended: false, changesUid: true, regenerates: ['dataElement:age'] },
    ],
  },
  {
    id: 'q-2',
    workflowId: W,
    projectId: P,
    stageId: STAGE_COMPILE,
    text: '哪些机构共用这套数据？',
    severity: 'MEDIUM',
    answerMode: 'MULTIPLE',
    blocking: false,
    status: 'OPEN',
    affects: ['orgUnit:facility'],
    suggestions: [
      { id: 'f1', text: 'Kenyatta 医院', rationale: '现有机构，直接复用。', recommended: true },
      { id: 'f2', text: 'Mbagathi 医院', rationale: '现有机构，直接复用。', recommended: true },
      { id: 'f3', text: '新建试点机构', rationale: '会新增机构并改变数据范围。', recommended: false, changesUid: true },
    ],
  },
];

/** name → the row S1 would return. Values are accepted and dropped (S5IF-G09). */
const secretStore = new Map();

// The model registry, as live rows: the console can now register and correct a model from a phone,
// and a harness that answered the write correctly but kept listing the old row would let the edit
// screen pass a click test on a change nobody can see back.
const modelRows = [
  {
    ref: 'openai/gpt-x-large',
    id: 'openai/gpt-x-large',
    provider: 'openai',
    model: 'gpt-x-large',
    providerClass: 'APPROVED_EXTERNAL',
    endpoint: 'https://api.openai.example/v1',
    apiKeySecretRef: 'vault://models/openai/key',
    capabilities: { structuredOutput: true, vision: true, maxContextTokens: 128000 },
    unitPriceInputPer1k: 0.01,
    unitPriceOutputPer1k: 0.03,
    enabled: true,
    applicableAgents: { REQUIREMENTS: 2 },
  },
];

// M4 (CP-UI-023): the three project-scoped tables, as live rows rather than canned responses. A
// members screen that answered a PUT correctly but listed the old set — or an authorisation screen
// whose revoke left the grant looking standing — would pass a click test on a write the console
// never shows back, so every handler below mutates these and every read re-serialises them.
const ACCOUNT_ROWS = () => [...createdUsers, ...LOCK_ROWS];
const memberRows = [
  { id: 'mbr-1', projectId: P, userId: 'usr-ada', subject: 'ada@example.org', role: 'BUSINESS_ANALYST' },
  { id: 'mbr-2', projectId: P, userId: 'usr-larry', subject: 'larry@example.org', role: 'DHIS2_IMPLEMENTER' },
];
const grantRows = [
  { id: 'rgt-1', projectId: P, userId: 'usr-ada', subject: 'ada@example.org', classification: 'PHI_PII', reason: '核对个人健康信息的抽样结果', grantedBy: '测试员', expiresAt: null, revoked: false },
];
/**
 * These three tables are written by the M4 pages, so a spec that revokes a row leaves the next
 * spec — or the next viewport of the same spec — looking at a server that already answered once.
 * Defaults are kept separately and restored by the reset every spec passes through.
 */
const EXEMPTION_DEFAULTS = [
  { id: 'pex-1', projectId: P, environmentKey: 'dev', waiver: 'NON_PRODUCTION_VERIFIED', reason: '试点环境暂无法完成非生产校验，限时放行', grantedBy: '测试员', expiresAt: new Date(Date.now() + 7 * 86_400_000).toISOString(), revoked: false },
  { id: 'pex-2', projectId: P, environmentKey: 'dev', waiver: 'NON_PRODUCTION_VERIFIED', reason: '上一轮验收窗口', grantedBy: '测试员', expiresAt: new Date(Date.now() - 86_400_000).toISOString(), revoked: true },
];

const MEMBER_DEFAULTS = JSON.parse(JSON.stringify(memberRows));
const GRANT_DEFAULTS = JSON.parse(JSON.stringify(grantRows));
const exemptionRows = JSON.parse(JSON.stringify(EXEMPTION_DEFAULTS));
/** Restore the M4 tables a spec may have written to, so no row state leaks into the next run. */
function resetM4Rows() {
  exemptionRows.splice(0, exemptionRows.length, ...JSON.parse(JSON.stringify(EXEMPTION_DEFAULTS)));
  if (typeof memberRows !== 'undefined') memberRows.splice(0, memberRows.length, ...JSON.parse(JSON.stringify(MEMBER_DEFAULTS)));
  if (typeof grantRows !== 'undefined') grantRows.splice(0, grantRows.length, ...JSON.parse(JSON.stringify(GRANT_DEFAULTS)));
}
/** `identity/service.py:GRANT_CLASSIFICATIONS` and `:EXEMPTION_WAIVERS`, the two accepted vocabularies. */
const GRANT_CLASSIFICATIONS = ['RESTRICTED', 'PHI_PII'];
const EXEMPTION_WAIVERS = ['NON_PRODUCTION_VERIFIED'];
const badField = (res, cors, path, message) => {
  res.writeHead(422, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
  res.end(JSON.stringify({ errorCode: 'VALIDATION_ERROR', message: `mock: ${message}`, details: { fields: [{ path, message }] } }));
};
const notFoundBody = (message, details = {}) => ({ errorCode: 'NOT_FOUND', message: `mock: ${message}`, details });

// Governance providers, held as rows so a PATCH (enable/correct) and the S5IF-GOV20 probe answer
// about the same registration the list shows.
const providerRows = [{ name: 'openai', providerClass: 'APPROVED_EXTERNAL', endpoint: 'https://api.openai.example/v1', apiKeySecretRef: 'vault://models/openai/key', adapter: 'openai_compat', enabled: true }];
// `/_control?mode=provider-down&provider=openai` marks one address unreachable, which is the arm the
// probe button exists for.
const unhealthyProviders = new Set();
// `/_control?mode=patch-closed` closes the REQUIREMENTS stage the way S1 does when it stops
// emitting `patch.submit` for it: the action disappears and the stage reports PASSED. Without this
// lever the entry page's closed-stage arm — the one that sends an operator to a change request —
// has no harness to be tested against.
let patchClosed = false;

const routes = {
  'GET /projects': () => envelope(bare ? [] : [
    projectRow,
    // What this run imported shows up here, so an "import accepted; the new project is X" toast is
    // followed by a list that actually contains X.
    ...importedProjects,
  ]),
  [`GET /projects/${P}`]: () => projectRow,
  [`GET /projects/${P}/workflow`]: () => ({ id: W, projectId: P, status: 'IN_PROGRESS', currentStageId: STAGE_COMPILE, currentStageType: 'BASELINE_COMPILE' }),
  [`GET /workflows/${W}`]: () => ({ id: W, projectId: P, status: 'IN_PROGRESS' }),
  [`GET /workflows/${W}/stages`]: () => envelope([
    // READY, not PASSED: S1 only answers `patch.submit` for a stage in READY|RUNNING|WAITING_APPROVAL
    // (`domain/actions.py`), so a harness that kept this PASSED could never exercise the hand-authoring
    // page's happy path — only its closed-stage arm.
    { id: 'stage-requirements', workflowId: W, stageType: 'REQUIREMENTS', status: patchClosed ? 'PASSED' : 'READY' },
    { id: 'stage-domain', workflowId: W, stageType: 'DOMAIN', status: 'PASSED' },
    { id: STAGE_COMPILE, workflowId: W, stageType: 'BASELINE_COMPILE', status: 'READY', inputCompleteness: 1 },
    { id: 'stage-handoff', workflowId: W, stageType: 'HANDOFF', status: 'LOCKED' },
  ]),
  [`GET /workflows/${W}/allowed-actions`]: () => ({
    scope: 'workflow',
    workflowId: W,
    evaluatedAt: new Date().toISOString(),
    allowed: [
      { action: 'stage.run', targetId: STAGE_COMPILE, available: true },
      { action: 'input.upload', available: true },
      // The hand-authoring gate. S1 emits it per stage (`targetId` is the stage that owns the
      // section), and the 需求条目 page refuses every write without it — so the harness has to
      // answer it with the same shape or the page is tested only in its disabled state.
      ...(patchClosed ? [] : [{ action: 'patch.submit', targetId: 'stage-requirements', available: true }]),
      // CP-UI-016 / FR-DATA-008: the bytes and their destruction are both S1's forwarding answers
      // (`domain/actions.py:94-112`), and the artifact page gates each of its two controls on one.
      { action: 'artifact.download', available: true },
      { action: 'artifact.purge', available: true },
      { action: 'input.parse', targetId: 'in-1', available: true },
      { action: 'input.confirm_column_mapping', targetId: 'in-1', available: true },
      { action: 'gate.approve', targetId: 'gate-1', available: true },
      { action: 'gate.approve', targetId: 'gate-warn', available: true },
      { action: 'execution.commit', targetId: 'run-1', available: true },
      // S1 answers drift-check per run and refuses it once the run is closed (`NO_OPEN_RUN`); the
      // harness's run is open, so the re-check control has to be offered here or it is permanently
      // grey in every mobile test.
      { action: 'deployment_run.drift_check', targetId: 'run-1', available: true },
      { action: 'question.answer', targetId: 'q-1', available: true },
      // CP-UI-023 has been delivered: this deployment runs with M4 live, so the three project-scoped
      // rights are answered here rather than in the platform list. S1 puts them on the *workflow*
      // answer (`domain/actions.py:project_actions()`) with no target id, because they are evaluated
      // against this project — the platform answer, evaluated with no project, would tell a project's
      // own member manager that they may not manage it.
      { action: 'member.manage', available: true },
      { action: 'restricted_grant.manage', available: true },
      { action: 'production_exemption.manage', available: true },
      // project_actions() answers these for a non-archived project; the settings screen gates its
      // detail save and its danger zone on them, so the harness has to answer them too.
      { action: 'project.edit', available: true },
      { action: 'project.archive', available: true },
      { action: 'project.export', available: true },
      // CP-UI-021: S1 answers the development package action from its own artifact index
      // (`domain/actions.py:695`), and the Handoff page gates on it. `uncompiled` is the lever that
      // withholds the package below, so it withholds the action here too — the two answers have to
      // agree or the harness teaches the page to trust a gate that the listing contradicts.
      ...(uncompiled ? [] : [{ action: 'frontend_package.download', available: true }]),
    ],
    // A project-scoped right that is present but refused, so the reason path is exercised too:
    // `archive` needs a project that is not archived, and the answer must say why.
    unavailable: [
      { action: 'project.delete', targetId: P, available: false, reasonCode: 'STAGE_NOT_PASSED', details: { requires: 'ARCHIVED' } },
      ...(uncompiled ? [{ action: 'frontend_package.download', available: false, reasonCode: 'PACKAGE_NOT_GENERATED', details: {} }] : []),
    ],
  }),
  [`GET /projects/${P}/blueprint/entries`]: (query) => {
    // S5IF-BP04, the read the 需求条目 page is built on. The locators are attached here rather than
    // stored, because stripping them is exactly what the page's own mapper is tested on; the rows
    // come back in `businessKey` order, which is what S2's read model does and the page must not
    // mistake for the stored array order.
    const source = String(query?.get?.('source') ?? 'DRAFT');
    const rows = source === 'APPROVED' ? [] : requirementRows;
    return {
      projectId: P,
      version: '1.0.0',
      source,
      entries: rows.map((row) => ({ ...row, _section: 'requirements', _path: `/requirements/items/${row.businessKey}` })),
      notFound: [],
    };
  },
  [`GET /projects/${P}/blueprint/section-schemas/requirements`]: () => requirementsSectionSchema(),
  [`GET /projects/${P}/blueprint/section-schemas/domain`]: () => sectionSchema(),
  [`GET /projects/${P}/blueprint/section-schemas`]: () => envelope([{ section: 'domain' }, { section: 'mapping' }]),
  [`GET /projects/${P}/blueprint/draft`]: () => ({ document: { domain: { entities: [] } } }),
  'GET /environments': () => envelope(bare ? [] : [{ environmentKey: 'dev', name: 'DEV', environmentClass: 'DEV', status: 'ACTIVE' }]),
  // S5IF-SET01. The guided checklist reads this; answering it from the same fixtures the other
  // routes use keeps `/start` and the pages it links to telling the same story.
  'GET /setup': () => ({
    generatedAt: new Date().toISOString(),
    engine: { name: 'inprocess', durable: false, detail: { note: 'mock' } },
    providers: { S2: 'real', S3: 'real', S4: 'real' },
    startup: null,
    configuration: { providerMode: 'auto', workflowEngine: 'inprocess', migrationsMode: 'apply', httpsOnly: true, authMode: 'off', problems: [] },
    identity: { authMode: 'off', enforced: false, m4Enabled: false, bootstrapAdminDeclared: false, users: 0, pendingPasswordChange: false },
    environments: {
      total: 1, inService: 1, nonProductionInService: 1, neverTested: [],
      items: [{ environmentKey: 'dev', environmentClass: 'DEV', status: 'ACTIVE', lastTestedAt: '2026-09-30T08:00:00Z' }],
    },
    credentials: { storeAvailable: true, masterKeyConfigured: true, scheme: 'db', named: secretStore.size, unresolvable: [] },
    intelligence: { providers: 1, providersEnabled: 1, models: 1, modelsEnabled: 1 },
    projects: { total: 1, active: 1 },
    // `bare` has to be spread LAST: an earlier spread is overwritten by the literals below it,
    // which made the cold-start lever a no-op.
    ...(bare
      ? {
          providers: { S2: 'fake', S3: 'fake', S4: 'fake' },
          environments: { total: 0, inService: 0, nonProductionInService: 0, neverTested: [], items: [] },
          intelligence: { providers: 0, providersEnabled: 0, models: 0, modelsEnabled: 0 },
          credentials: { storeAvailable: true, masterKeyConfigured: true, scheme: 'db', named: 0, unresolvable: [] },
          projects: { total: 0, active: 0 },
        }
      : {}),
  }),
  // S5IF-SEC02: names only. A mock that echoed the submitted value back would let a test pass on
  // something the real S1 refuses to do (S5IF-G09).
  'GET /secrets': () => ({ items: [...secretStore.values()].map((row) => ({ ...row })), total: secretStore.size, generatedAt: new Date().toISOString() }),
  // S5IF-GOV07: the same three fields the real catalogue promises, and no reachability field.
  'GET /governance/provider-catalogue': () => ({
    items: [
      { key: 'openai', displayName: 'OpenAI', endpoint: 'https://api.openai.com/v1', wireProtocol: 'openai_compat', models: ['gpt-4o', 'gpt-4o-mini'], keyRefSuggestion: 'env://OPENAI_API_KEY', docsUrl: 'https://platform.openai.com/docs/api-reference', local: false, note: '' },
      { key: 'deepseek', displayName: 'DeepSeek', endpoint: 'https://api.deepseek.com/v1', wireProtocol: 'openai_compat', models: ['deepseek-chat'], keyRefSuggestion: 'env://DEEPSEEK_API_KEY', docsUrl: 'https://api-docs.deepseek.com/', local: false, note: '' },
      // 契约 1.7.1: a `local` entry has no key to suggest — the real catalogue answers '' so the
      // form can say "leave this empty" instead of prompting for a name that cannot resolve.
      { key: 'ollama', displayName: 'Ollama', endpoint: 'http://localhost:11434/v1', wireProtocol: 'openai_compat', models: ['qwen2.5'], keyRefSuggestion: '', docsUrl: 'https://github.com/ollama/ollama/blob/main/docs/openai.md', local: true, note: 'mock' },
    ],
    total: 3,
  }),
  'GET /governance/models': () => envelope(modelRows),
  'GET /governance/providers': () => envelope(providerRows),
  'GET /governance/policy': () => ({ effective: { allowed: { PUBLIC: ['LOCAL_ONLY', 'PRIVATE_NETWORK'], PHI_PII: [] }, sources: [], warnings: [] }, ceiling: { allowed: { PUBLIC: ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL'], PHI_PII: [] }, sources: [], warnings: [] }, isDefault: true, updatedBy: 'mock' }),
  'GET /governance/usage': () => ({ items: [], total: 0 }),
  'GET /governance/prompts': () => envelope([]),
  'GET /governance/evaluations': () => envelope([]),
  // The eight agents of the AI-AG table. An empty list here hid the whole applicability block of the
  // model registration sheet from every end-to-end check, which is the part a finger has to hit.
  'GET /governance/agents': () => envelope([
    { agentType: 'REQUIREMENTS', stage: 'REQUIREMENTS', enabled: true, promptName: 'requirements.consolidate', patchMode: 'PATCH' },
    { agentType: 'DOMAIN', stage: 'DOMAIN', enabled: true, promptName: 'domain.model', patchMode: 'PATCH' },
    { agentType: 'MAPPING', stage: 'MAPPING', enabled: true, promptName: 'mapping.decide', patchMode: 'PATCH' },
    { agentType: 'METADATA_DESIGN', stage: 'MAPPING', enabled: true, promptName: 'metadata_design.expression', patchMode: 'PATCH' },
    { agentType: 'API', stage: 'PAGE_DESIGN', enabled: true, promptName: 'api.design', patchMode: 'PATCH' },
    { agentType: 'UI', stage: 'PAGE_DESIGN', enabled: true, promptName: 'ui.design', patchMode: 'PATCH', perPage: true },
    { agentType: 'FIXTURE', stage: 'PAGE_DESIGN', enabled: true, promptName: 'fixture.specify', patchMode: 'PATCH', perPage: true },
    { agentType: 'REVIEW', stage: 'REQUIREMENTS', enabled: true, promptName: 'review.explain', patchMode: 'EXPLAIN_ONLY' },
  ]),
  'GET /governance/budget': () => ({ limit: null, currency: 'USD', spent: 0, thresholds: [], state: 'UNSET' }),
  'GET /governance/prompts/activations': () => envelope([]),
  'GET /gates': () => envelope([gate('gate-1', 'REQUIREMENT_GATE', []), gate('gate-warn', 'PACKAGE_GATE', [{ issueId: 'w1', level: 'WARNING', message: '选项集顺序与需求不一致' }])]),
  'GET /gates/gate-1': () => gate('gate-1', 'REQUIREMENT_GATE', []),
  'GET /gates/gate-warn': () => gate('gate-warn', 'PACKAGE_GATE', [{ issueId: 'w1', level: 'WARNING', message: '选项集顺序与需求不一致' }]),
  // CP-GATE-019's real shape (`S1 services/gates.py:explanation`): `found` and `willDo` are objects,
  // `why` is a map of category → the decisions under it, `canChange` is a list of affordances. The
  // harness used to answer with `[]`s, so the two questions the console asks of a gate (`why`,
  // `canChange`) rendered as nothing here and no test on the phone could see them at all.
  'GET /gates/gate-1/explanation': () => explanation('gate-1', 'REQUIREMENT_GATE', []),
  'GET /gates/gate-warn/explanation': () => explanation('gate-warn', 'PACKAGE_GATE', [{ issueId: 'w1', level: 'WARNING', message: '选项集顺序与需求不一致' }]),
  'GET /questions/q-1': () => mockQuestions[0],
  'GET /questions/q-2': () => mockQuestions[1],
  [`GET /workflows/${W}/questions`]: () => envelope(mockQuestions),
  'GET /deployments': () => envelope([]),
  'GET /deployment-runs': () => envelope([run()]),
  [`GET /workflows/${W}/deployment-runs`]: () => envelope([run()]),
  'GET /deployment-runs/run-1': () => run(),
  'GET /deployment-runs/run-1/summary': () => summary(),
  'GET /deployment-runs/run-1/environment-bindings': () => envelope([]),
  'GET /executions/e-1': () => ({ id: 'e-1', status: 'SUCCEEDED' }),
  'GET /stages/stage-compile': () => ({ id: STAGE_COMPILE, workflowId: W, stageType: 'BASELINE_COMPILE', status: 'READY' }),
  'GET /deployment-runs': () => envelope([]),
  'GET /notifications': () => envelope([
    { id: 'n-fail', type: 'EXECUTION_FAILED', projectId: P, read: false, createdAt: new Date().toISOString(), message: '提交失败：目标环境在校验之后被改动，请重新校验', objectRef: { type: 'run', id: 'run-1' } },
    { id: 'n-q', type: 'QUESTION_CREATED', projectId: P, read: false, createdAt: new Date().toISOString(), message: '有一个问题等待回答', objectRef: { type: 'question', id: 'q-1' } },
  ]),
  // M4 reads (CP-UI-023). Each is the live table above, so the list a page re-reads after a write is
  // the table that write left behind.
  [`GET /projects/${P}/members`]: () => envelope(memberRows),
  [`GET /projects/${P}/artifact-grants`]: () => envelope(grantRows),
  [`GET /projects/${P}/production-exemptions`]: () => envelope(exemptionRows),
  [`GET /projects/${P}/upload-limits`]: () => ({
    // Contract shape: allowedUploadTypes + maxUploadBytes. Without this route the size and type
    // prechecks were never exercised in e2e at all.
    allowedUploadTypes: ['.xlsx', '.csv', '.json', '.md', '.txt'],
    maxUploadBytes: 2097152,
  }),
  [`GET /projects/${P}/inputs`]: () => envelope([
    // Every upload the harness accepted, newest first: the screen promises the row shows up after a
    // successful upload, and a list that never grows cannot show that promise being kept.
    ...uploadedInputs,
    // `InputView` field names, not invented ones: id/name/type do not exist on this view, and the
    // screen read them as if they did.
    {
      artifactId: 'in-1',
      filename: '需求表.xlsx',
      mediaType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      sizeBytes: 20480,
      parseStatus: 'NEEDS_REVIEW',
      dataClassification: 'INTERNAL',
      contentHash: 'sha256:in-1',
    },
  ]),
  [`GET /workflows/${W}/artifacts`]: () => envelope(uncompiled ? [] : [{ id: 'art-openapi', type: 'OPENAPI', workflowId: W }]),
  // The detail row the mobile artifact page reads. It was missing: the fallthrough answered an empty
  // envelope, so the page rendered a card with no type, no version and no hash in every harness run —
  // a screen that looked "tested" while nothing about its content was ever checked.
  'GET /artifacts/art-openapi': () => artifact('art-openapi'),
  'GET /artifacts/art-openapi/content': () => (purgedArtifacts.has('art-openapi')
    ? { errorCode: 'ARTIFACT_PURGED', message: 'mock: the bytes are gone, the record stays', details: {} }
    : OPENAPI_YAML),
  [`GET /projects/${P}/package-files`]: () =>
    uncompiled ? { kind: 'NONE', files: [] } : { kind: 'HANDOFF', version: '1.0.0', buildId: 'b-1', environmentKey: 'dev', packageHash: 'sha256:abc', files: [{ path: 'api/openapi.yaml', role: 'API' }] },
  // CP-UI-021: the mode-B development package. `kind` here is S2's word for a file's role in the
  // dev package and deliberately not the `role` above, because the two packages answer different
  // questions; no package is the NOT_GENERATED arm (CP-UI-022) the guidance screen is built for.
  [`GET /projects/${P}/frontend-package/files`]: () => (uncompiled
    ? { errorCode: 'NOT_FOUND', message: 'mock: no package yet', details: { reason: 'NOT_GENERATED', requiredStage: 'HANDOFF' } }
    : { projectId: P, artifactId: 'art-hof', blueprintVersion: '1.0.0', files: [
      { path: 'README.md', kind: 'DOCUMENT', sizeBytes: 1840, contentHash: 'sha256:hf0' },
      { path: 'src/api/dataElements.ts', kind: 'CONTRACT', sizeBytes: 2410, contentHash: 'sha256:hf1' },
    ] }),
  [`GET /workflows/${W}/object-graph`]: () => (uncompiled ? notFound() : graph(60)),
  [`GET /projects/${P}/registry`]: () => (uncompiled ? notFound() : envelope(registryRows)),
  [`GET /workflows/${W}/validation-issues`]: () => envelope([]),
  'GET /auth/session': () => sessionView(),
  // UM-AUTH-001, the leaving half. The redirect URI is echoed rather than configured, which is how
  // S1 treats it too: the same string has to come back on the exchange.
  'GET /auth/oidc/request': () => {
    if (!oidcOn) return { errorCode: 'INVALID_STATE', message: 'mock: this deployment does not use OIDC sign-in', details: {} };
    const redirectUri = new URLSearchParams(globalThis.__q ?? '').get('redirectUri') ?? '';
    return { ...OIDC, state: OIDC_STATE, redirectUri };
  },
  'GET /allowed-actions': () => ({
    scope: 'platform',
    evaluatedAt: new Date().toISOString(),
    allowed: [
      // `input.upload` / `input.parse` are answered on the workflow list by S1 (project_actions),
      // not here. Answering them at platform scope is what let the mobile upload button look fine in
      // e2e while a real S1 greyed it out.
      { action: 'project.create', available: true },
      // UI-PRJ-004 pairs 导出 with 导入; `POST /projects/import` authorizes this name and S1 puts it
      // on the platform list (`domain/actions.py:PLATFORM_ACTIONS`), because the thing being created
      // is the project the button would otherwise have to name.
      { action: 'project.import', available: true },
      { action: 'secret.manage', available: true },
      // The governance provider row: editing, enabling and probing one another are all
      // platform-scope answers in S1 (`domain/actions.py`), and the mobile row gates its three
      // controls on this one — without it every button there is inert in the harness.
      { action: 'governance.provider.enable', available: true },
      // The access screen's user management is gated on this right (UM-BR-01: a missing entry is
      // unavailable, never assumed), so without it the create/assign controls stayed disabled in the
      // harness and no test had ever pressed one — the account screens were unproven, not passing.
      { action: 'user.manage', available: true },
      // The guided checklist links at these pages, so the harness has to answer for their controls
      // the way a real S1 does — otherwise `/start` points at screens whose buttons are all off.
      { action: 'environment.create', available: true },
      { action: 'environment.edit', available: true, targetId: 'dev' },
      { action: 'environment.test_connection', available: true, targetId: 'dev' },
      { action: 'environment.probe_capabilities', available: true, targetId: 'dev' },
      { action: 'environment.disable', available: true, targetId: 'dev' },
      { action: 'environment.emergency_lock', available: true, targetId: 'dev', details: { locked: false } },
      { action: 'governance.provider.register', available: true },
      { action: 'governance.model.register', available: true },
      { action: 'governance.model.enable', available: true },
      { action: 'governance.policy.relax', available: true },
    ],
    unavailable: [],
  }),
  // Only with an account deployment and the `lock-user` lever, so the other specs keep seeing the
  // empty list they were written against. The point is the row the consoles have to render honestly:
  // a locked account is still `status: ACTIVE` with `locked: true`, and a tag that spells ACTIVE in
  // red reads like a colour bug rather than a frozen account.
  'GET /users': () => (authOn && lockedRow
    ? envelope(LOCK_ROWS.map((u) => ({ ...u, roles: u.roles.map((r) => ({ ...r })) })))
    // ... and an account the app just created has to come back on the re-read with the display name
    // the operator typed. A create that returned 201 but listed nothing would pass a click test and
    // still leave the console unable to show who it made.
    : envelope(createdUsers.map((u) => ({ ...u, roles: (u.roles ?? []).map((r) => ({ ...r })) })))),
  // The role catalogue S1 issues (`identity/roles.py:BUILT_IN_ROLES`). Returning an empty list made
  // the assign sheet offer nothing to pick and the roles tab nothing to read, so the whole
  // grant/revoke path was unexercised rather than passing.
  'GET /roles': () => envelope(
    ['SOLUTION_ARCHITECT', 'DHIS2_IMPLEMENTER', 'FRONTEND_DEVELOPER', 'BUSINESS_ANALYST', 'DEPLOYMENT_APPROVER', 'PLATFORM_ADMINISTRATOR']
      .map((name) => ({ name, actions: name === 'PLATFORM_ADMINISTRATOR' ? ['user.manage', 'project.create', 'settings.manage'] : ['project.edit'] })),
  ),
};

function explanation(id, type, warnings) {
  return {
    gateId: id,
    gateType: type,
    found: { boundObjects: [{ refType: 'ARTIFACT', refId: 'art-req-1', contentHash: 'sha256:9c1f0a4b7d2e4a1f8c3b5d6e7f8091a2' }] },
    willDo: { gateType: type, environmentKey: null, riskSummaryRef: null, confirmationItems: [] },
    why: {
      decisions: [
        {
          decisionId: 'MAP-001',
          subject: '用 categoryOption 表达年龄段，而不是新建一组 dataElement',
          rationale: '既有统计口径不能改，选项集可以复用',
          type: 'MAPPING',
          impactLevel: 'HIGH',
          confidence: 0.8,
        },
      ],
    },
    uncertain: { warnings, openQuestions: [] },
    canChange: [
      { action: 'question.answer', target: 'open questions', invalidatesGate: false },
      { action: 'stage.rerun', target: 'this stage', invalidatesGate: true },
    ],
    afterApproval: { nextStageType: 'DOMAIN' },
  };
}

function gate(id, type, warnings) {
  return {
    id,
    workflowId: W,
    projectId: P,
    stageId: STAGE_COMPILE,
    status: 'WAITING',
    // S1's own tokens: the console names a gate by the work it closes, and a harness that invents
    // its own type words leaves that mapping — and any test of it — pointing at nothing
    type,
    environmentKey: 'dev',
    requiredApprovals: 1,
    approvalCount: 0,
    unresolvedWarningCount: warnings.filter((w) => !w.disposition).length,
    highOpenQuestionCount: 0,
    createdAt: '2026-10-03T08:00:00Z',
    approvals: [],
    boundObjects: [],
    warnings,
    // A required confirmation exists only on the warning gate, so the two §6.1 approval budgets
    // (2 without warnings, 4 with) exercise genuinely different paths.
    confirmationItems: [],
  };
}

function run() {
  return { id: 'run-1', workflowId: W, projectId: P, stageId: STAGE_COMPILE, environmentKey: 'dev', status: 'READY', executionId: 'e-1', blueprintVersion: '1.0.0' };
}

/**
 * Artifacts whose bytes were purged this session. `POST /artifacts/{id}/purge` adds the id here, and
 * the detail row, its download affordance and the content read (410) all follow — otherwise the
 * confirmation loop UM-P-04 asks for could not be played end to end, only asserted about.
 */
const purgedArtifacts = new Set();

/** Projects this run restored through `POST /projects/import`; `GET /projects` shows them. */
const importedProjects = [];
// The blueprint draft's requirement entries (S5IF-BP04) — what the 需求条目 page reads and writes.
// Hand-authored rows go in here, so a create followed by a list really shows the new entry and the
// harness cannot hide a page that writes to nothing.
const requirementRows = [
  {
    businessKey: 'req-mock-delivery', statement: '每一条接种记录都要能追溯到填报人。',
    status: 'CANDIDATE', priority: 'MUST', confidence: 0.8, source: { note: 'mock 手工条目' },
  },
  {
    businessKey: 'req-mock-report', statement: '月度汇总要能按接种点分组导出。',
    status: 'CONFIRMED', priority: 'SHOULD', confidence: 0.9, source: { artifactId: 'in-1', location: 'sheet:需求表' },
  },
];
// Uploads the harness accepted, newest first, so the material list grows the way the screen promises.
const uploadedInputs = [];

/** `ArtifactView` by its own field names (FR-DATA-008: the record survives, the bytes do not). */
function artifact(id) {
  return {
    id,
    workflowId: W,
    projectId: P,
    type: 'OPENAPI',
    version: 1,
    status: 'APPROVED',
    contentHash: 'sha256:art1',
    size: 4200,
    mediaType: 'application/vnd.oai.openapi',
    dataClassification: 'INTERNAL',
    creatorSubsystem: 'S2',
    creatorOperator: 'tester',
    createdAt: '2026-03-03T08:30:00Z',
    contentPurged: purgedArtifacts.has(id),
    sourceArtifactIds: ['in-1'],
  };
}

function summary() {
  return {
    runId: 'run-1', workflowId: W, projectId: P, projectName: '免疫规划试点', environmentKey: 'dev', environmentClass: 'TEST',
    status: 'READY', blueprintVersion: '1.0.0', profileVersion: '1', evaluatedAt: new Date().toISOString(),
    approval: { required: false, granted: true }, commitWindow: { open: true, windows: [] }, connection: { ok: true },
    drift: { detected: false }, capabilityFreshness: { fresh: true }, verification: { passed: true }, validation: { passed: true },
    changeStats: { create: 1, update: 0 }, stages: [], gates: [], execution: { executionId: 'e-1', status: 'READY' },
    snapshot: {}, payload: {}, maintenanceActions: [], outdated: false,
  };
}

function notFound() {
  // The pre-CP-UI-022 shape S1 returns today: NOT_FOUND with no details.reason.
  return { errorCode: 'NOT_FOUND', message: 'no blueprint version', details: { projectId: P } };
}

/** S1 SRS §8.3 (S1IF-G07): the error code fixes the HTTP status, so the mock must not answer 200. */
const HTTP_BY_CODE = {
  OPERATOR_REQUIRED: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  CSRF_FAILED: 403,
  NOT_FOUND: 404,
  INVALID_STATE: 409,
  REQUEST_IN_PROGRESS: 409,
  APPROVAL_INVALIDATED: 409,
  ENVIRONMENT_LOCKED: 409,
  OUTSIDE_COMMIT_WINDOW: 409,
  PRIOR_VERIFICATION_MISSING: 409,
  PROJECT_ARCHIVED: 409,
  BUDGET_EXCEEDED: 409,
  FEATURE_NOT_ENABLED: 409,
  IDEMPOTENCY_CONFLICT: 422,
  UPLOAD_REJECTED: 422,
  VALIDATION_ERROR: 422,
  ARTIFACT_PURGED: 410,
  RATE_LIMITED: 429,
  INTERNAL_ERROR: 500,
  PROVIDER_UNAVAILABLE: 502,
  SERVICE_UNAVAILABLE: 503,
  TIMEOUT: 504,
};
const httpStatusOf = (body) => (body && typeof body === 'object' && HTTP_BY_CODE[body.errorCode]) || 200;

/**
 * The project's Registry entries, as one list.
 *
 * A graph node *is* a Registry entry (BP-TD-006), and the top search finds objects by reading the
 * Registry — so the two reads have to speak about the same rows. They used to be written apart, and
 * the search→graph jump then landed on a canvas with no such node: the URL was right, the selection
 * was nothing, and only a browser run could tell.
 */
const registryRows = [
  { businessKey: 'dataelement.a', dhis2Type: 'dataelement', uid: 'U900', managed: true, disposition: 'CREATE' },
  { businessKey: 'optionset.b', dhis2Type: 'optionset', uid: 'U901', managed: true, disposition: 'UPDATE' },
];

function graph(n) {
  const nodes = registryRows.map((row, i) => ({ ...row, inPayload: true, inDegree: i === 0 ? 1 : 0, outDegree: i === 0 ? 0 : 1 }));
  const edges = [];
  const types = ['dataelement', 'optionset', 'indicator'];
  // the generated run starts after the Registry rows, and `i > 0` below is the edge rule
  // that keeps every node but the first attached to the line
  for (let i = registryRows.length; i < n; i++) {
    // BP-TD-006: a graph node is a Registry entry and nothing else — no status, no code, no
    // bindings here; those live in the registry read
    nodes.push({ businessKey: `${types[i % 3]}.${String(i).padStart(5, '0')}`, dhis2Type: types[i % 3], uid: i % 30 === 0 ? null : `U${i}`, managed: true, inPayload: i % 4 !== 0, inDegree: i % 50 === 0 ? 0 : 1, outDegree: i % 50 === 0 ? 0 : 1, disposition: i % 2 ? 'CREATE' : 'UPDATE' });
    if (i > 0) edges.push({ edgeKind: 'ref', field: 'dataElements', fromBusinessKey: nodes[i].businessKey, toBusinessKey: nodes[i - 1].businessKey });
  }
  return {
    workflowId: W,
    projectId: P,
    kind: 'BASELINE',
    blueprintVersion: '1.0.0',
    buildId: 'b-1',
    runId: null,
    schemaVersion: 1,
    environmentKey: null,
    nodes,
    edges,
    orphans: nodes.filter((_, i) => i % 50 === 0).map((x) => x.businessKey),
    filters: { dhis2Types: [], businessKeys: [] },
    unresolvedRegistryRefs: [{ fromBusinessKey: nodes[nodes.length - 1].businessKey, field: 'categoryCombo', target: 'ghost.does-not-exist' }],
  };
}

function sectionSchema() {
  return {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    $id: 'urn:damf:bp:1:mock:domain',
    type: 'object',
    additionalProperties: false,
    required: ['deliveryMode'],
    properties: {
      deliveryMode: { $ref: '#/definitions/deliveryMode' },
      entities: { type: 'array', items: { $ref: '#/definitions/entity' } },
    },
    definitions: {
      deliveryMode: { type: 'string', enum: ['A', 'B'] },
      entity: { type: 'object', additionalProperties: false, required: ['businessKey'], properties: { businessKey: { type: 'string' }, displayName: { type: 'string' } } },
    },
  };
}

/**
 * The `requirements` section schema, shaped like the document S1 serves (S5IF-BP02): S2 routes
 * businessKey / confidence / source through `#/definitions/*`, and the entry form reads its limits
 * out of that — so a mock that inlined them would let the page pass on a schema the server never
 * sends.
 */
function requirementsSectionSchema() {
  return {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    $id: 'urn:damf:bp:1:mock:requirements',
    title: 'requirements',
    type: 'object',
    additionalProperties: false,
    required: ['items'],
    definitions: {
      businessKey: {
        type: 'string',
        pattern: '^[a-z][a-z0-9]*([._:-][A-Za-z0-9_-]+)*$',
        minLength: 2,
        maxLength: 255,
      },
      confidence: { type: 'number', minimum: 0, maximum: 1 },
      source: {
        type: 'object',
        additionalProperties: false,
        properties: {
          artifactId: { type: 'string' },
          location: { type: 'string', maxLength: 512 },
          decisionId: { type: 'string' },
          note: { type: 'string', maxLength: 1024 },
        },
        anyOf: [{ required: ['artifactId'] }, { required: ['decisionId'] }, { required: ['note'] }],
      },
    },
    properties: {
      items: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['businessKey', 'statement', 'source'],
          properties: {
            businessKey: { $ref: '#/definitions/businessKey' },
            statement: { type: 'string', minLength: 1, maxLength: 4096 },
            rationale: { type: 'string', maxLength: 4096 },
            priority: { type: 'string', enum: ['MUST', 'SHOULD', 'COULD', 'WONT'] },
            status: { type: 'string', enum: ['CANDIDATE', 'CONFIRMED', 'REJECTED'] },
            confidence: { $ref: '#/definitions/confidence' },
            actors: { type: 'array', items: { $ref: '#/definitions/businessKey' }, uniqueItems: true },
            openQuestions: {
              type: 'array',
              items: {
                type: 'object',
                additionalProperties: false,
                required: ['question', 'severity'],
                properties: {
                  question: { type: 'string', maxLength: 2048 },
                  severity: { type: 'string', enum: ['LOW', 'MEDIUM', 'HIGH'] },
                },
              },
            },
            source: { $ref: '#/definitions/source' },
          },
        },
      },
    },
  };
}

const OPENAPI_YAML = [
  'openapi: 3.1.0',
  'info:',
  '  title: 免疫规划试点 API',
  '  version: 1.0.0',
  'tags:',
  '  - name: patient',
  'paths:',
  '  /api/patients:',
  '    get:',
  '      operationId: listPatients',
  '      summary: 列出患者',
  '      tags: [patient]',
      '      parameters:',
      '        - name: pageSize',
      '          in: query',
      '          schema: { type: integer }',
      '      responses:',
      '        "200":',
      '          description: ok',
      '          content:',
      '            application/json:',
      '              schema:',
      '                $ref: "#/components/schemas/PatientPage"',
      '  x-registry-refs: ["dataelement.age"]',
      'components:',
      '  schemas:',
      '    PatientPage:',
      '      type: object',
      '      properties:',
      '        items:',
      '          type: array',
      '          items:',
      '            $ref: "#/components/schemas/Patient"',
      '    Patient:',
      '      type: object',
      '      required: [businessKey]',
      '      properties:',
      '        businessKey:',
      '          type: string',
      '        meta:',
      '          $ref: "#/components/schemas/Patient"',
      '',
].join('\n');

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = url.pathname.replace(/^\/api\/v1/, '');
  globalThis.__q = url.search;

  const cors = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Expose-Headers': 'X-Correlation-Id' };

  // Test-only lever: one mock instance serves both the compiled and the not-yet-generated arms
  // of UM-AC-19 without a restart. Not part of the S1 contract.
  if (path === '/_control') {
    const mode = url.searchParams.get('mode');
    if (mode === 'uncompiled') uncompiled = true;
    if (mode === 'compiled') uncompiled = false;
    if (mode === 'bare') bare = true;
    if (mode === 'configured') bare = false;
    if (mode === 'sse-off') sseOff = true;
    if (mode === 'sse-on') sseOff = false;
    // UM-NET-009's two levers: an account deployment, and a session that has just expired.
    // `auth-off` is the reset every spec owes, so a lapse cannot leak into the next test file.
    if (mode === 'auth-on') { authOn = true; oidcOn = false; signedInNow = false; lapsed = false; createdUsers.length = 0; resetLockRows(); resetM4Rows(); }
    if (mode === 'auth-off') { authOn = false; oidcOn = false; signedInNow = false; lapsed = false; lockedRow = false; createdUsers.length = 0; resetLockRows(); resetM4Rows(); }
    // UM-AUTH-001: offer the organisation's door alongside the password form. Implies an account
    // deployment, and starts signed out — otherwise the SSO spec would inherit whoever the previous
    // file left logged in.
    if (mode === 'oidc-on') { authOn = true; oidcOn = true; signedInNow = false; lapsed = false; }
    if (mode === 'oidc-off') { oidcOn = false; }
    // A lever that only restores the M4 tables: resetting auth as a side effect would change what
    // the next test sees about whether accounts exist at all.
    if (mode === 'm4-reset') resetM4Rows();
    if (mode === 'lapse') lapsed = true;
    if (mode === 'no-lapse') lapsed = false;
    // S5IF-GOV20: make one provider's address answer as unreachable, so the probe button's failure
    // arm — the reason the button exists — is testable. `provider-up` resets for the next spec.
    if (mode === 'provider-down') unhealthyProviders.add(url.searchParams.get('provider') ?? 'openai');
    if (mode === 'provider-up') unhealthyProviders.clear();
    if (mode === 'patch-closed') patchClosed = true;
    if (mode === 'patch-open') patchClosed = false;
    if (mode === 'need-password') passwordChangePending = true;
    if (mode === 'no-need-password') passwordChangePending = false;
    if (mode === 'lock-user') lockedRow = true;
    if (mode === 'unlock-user') lockedRow = false;
    // FR-DATA-008's lever. Purging is a one-way fact about a row, so the harness needs the other way
    // too: `reuseExistingServer` means a spec that purged would otherwise leave the next run's
    // artifact page already purged, and its download assertion would fail for a reason it did not do.
    if (mode === 'artifact-purged') purgedArtifacts.add('art-openapi');
    if (mode === 'artifact-intact') purgedArtifacts.clear();
    if (mode === 'imports-reset') importedProjects.length = 0;
    res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ uncompiled, sseOff, bare, authOn, signedIn: signedInNow, lapsed, mustChangePassword: passwordChangePending, lockedRow, artifactPurged: purgedArtifacts.has('art-openapi'), imported: importedProjects.length }));
    return;
  }

  if (path.startsWith('/workflows/') && path.endsWith('/events')) {
    if (sseOff) {
      res.writeHead(503, { ...cors, 'content-type': 'application/json' });
      res.end(JSON.stringify({ errorCode: 'PROVIDER_UNAVAILABLE', message: 'sse disabled', details: {} }));
      return;
    }
    res.writeHead(200, { ...cors, 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
    // One frame, then end the stream: the client's reconnect + polling fallback (UM-NET-004) takes
    // over. Keeping a timer alive per connection leaked hundreds of intervals across an e2e run and
    // starved the mock for the later projects.
    res.write(`id: 0\nevent: stream.reset\ndata: {"reason":"mock-end"}\n\n`);
    res.end();
    return;
  }

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    // Writes are accepted with an echo of the request body so click-budget flows can complete.
    const body = [];
    req.on('data', (c) => body.push(c));
    req.on('end', () => {
      // Uploads arrive as multipart/form-data, so the body is frequently not JSON. Parsing it
      // unguarded threw inside the request handler and killed the shared server mid-suite, which
      // looked exactly like "every later flow is over budget".
      let echo = null;
      try {
        echo = body.length ? JSON.parse(Buffer.concat(body).toString('utf8')) : null;
      } catch {
        echo = { raw: Buffer.concat(body).toString('utf8').slice(0, 200) };
      }
      // UM-NET-009: a session that has expired is refused *as S1 refuses it* — a 401 with the
      // UNAUTHENTICATED envelope, at the HTTP status the code maps to. Answering 200 with an error
      // body here would leave the client's error mapping unreachable, which is the whole thing the
      // spec is about. `/auth/*` stays reachable: signing in has to work while nothing is signed in.
      if (authOn && lapsed && !path.startsWith('/auth/')) {
        res.writeHead(401, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ errorCode: 'UNAUTHENTICATED', message: 'mock: session lapsed', details: {} }));
        return;
      }
      // CP-IAM-004 / UM-NET-009: signing in mints a session; anything else is the same answer S1
      // gives, and the body is never echoed back — an echo of a login request would put the password
      // in the response, which is the exact shape of the mistake this harness already made once for
      // `/secrets`.
      if (path === '/auth/login') {
        // A lapsed session must still be able to sign back in — that is the whole recovery the app is
        // built around — so the refusal is about the credentials, never about the lapse.
        const ok = authOn && echo?.subject === ACCOUNT.subject && echo?.password === ACCOUNT.password;
        if (!ok) {
          res.writeHead(401, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'UNAUTHENTICATED', message: 'mock: the credentials did not match', details: {} }));
          return;
        }
        signedInNow = true;
        lapsed = false;
        // S1 mirrors the CSRF value into a *readable* cookie as well as the session view, because a
        // reloaded page has to be able to write again without signing in. The client reads it from
        // the cookie, so the harness has to set it or the double-submit header can never appear.
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock', 'Set-Cookie': 'factory_csrf=mock-csrf-1; Path=/; SameSite=Lax' });
        res.end(JSON.stringify(sessionView()));
        return;
      }
      // UM-AUTH-001, the return half. S1 compares the `state` the browser was sent against the
      // HttpOnly cookie it minted, so the client's one obligation is to echo it as a query parameter
      // — and a wrong echo has to be a refusal, not a login. The code itself is never echoed back:
      // it is a credential, and an echo would put it where a test could read it off the response.
      if (path === '/auth/oidc/exchange') {
        const state = new URLSearchParams(url.search).get('state') ?? '';
        if (!oidcOn || state !== OIDC_STATE) {
          res.writeHead(403, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'CSRF_FAILED', message: 'mock: the login state did not match', details: {} }));
          return;
        }
        if (!echo?.code) {
          res.writeHead(401, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'UNAUTHENTICATED', message: 'mock: the sign-in response carried no authorization code', details: {} }));
          return;
        }
        signedInNow = true;
        lapsed = false;
        // S1 mirrors the CSRF value into a *readable* cookie as well as the session view, because a
        // reloaded page has to be able to write again without signing in. The client reads it from
        // the cookie, so the harness has to set it or the double-submit header can never appear.
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock', 'Set-Cookie': 'factory_csrf=mock-csrf-1; Path=/; SameSite=Lax' });
        res.end(JSON.stringify(sessionView()));
        return;
      }
      if (path === '/auth/logout') {
        signedInNow = false;
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ cleared: true }));
        return;
      }
      // S5IF-GOV20: the reachability probe. An address the harness knows is down answers unhealthy
      // with the transport's own sentence, because the whole point of the button is to tell a wrong
      // address, a refused key and a slow model apart — an echo-shaped `{accepted:true}` here would
      // let a spec pass without ever proving the screen shows the difference.
      if (path.startsWith('/governance/providers/') && path.endsWith('/test-connection') && req.method === 'POST') {
        const name = decodeURIComponent(path.slice('/governance/providers/'.length, -'/test-connection'.length));
        const row = providerRows.find((p) => p.name === name);
        if (!row) {
          res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ref: name, healthy: false, detail: `mock: provider ${name} is not registered` }));
          return;
        }
        const down = unhealthyProviders.has(name);
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          ref: name,
          healthy: !down,
          detail: down ? 'model endpoint unreachable: ConnectError' : null,
        }));
        return;
      }
      // S5IF-GOV05 / GOV06: registering a model writes the whole row, correcting one moves only the
      // fields the body names. The second rule is the one a harness tends to skip and the one the
      // console now depends on: an edit that cleared the model's own address or Key reference would
      // otherwise look exactly like S1's answer.
      if (path === '/governance/models' && req.method === 'POST') {
        const ref = `${echo?.provider ?? 'openai'}/${echo?.model ?? ''}`;
        const row = {
          ref,
          id: ref,
          provider: String(echo?.provider ?? 'openai'),
          model: String(echo?.model ?? ''),
          providerClass: 'APPROVED_EXTERNAL',
          endpoint: 'https://api.openai.example/v1',
          apiKeySecretRef: 'vault://models/openai/key',
          capabilities: { structuredOutput: true, vision: false, maxContextTokens: 8000, ...(echo?.capabilities ?? {}) },
          unitPriceInputPer1k: Number(echo?.unitPriceInputPer1k ?? 0),
          unitPriceOutputPer1k: Number(echo?.unitPriceOutputPer1k ?? 0),
          enabled: echo?.enabled !== false,
          applicableAgents: { ...(echo?.applicableAgents ?? {}) },
        };
        const at = modelRows.findIndex((m) => m.ref === ref);
        if (at >= 0) modelRows.splice(at, 1, row);
        else modelRows.push(row);
        res.writeHead(201, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(row));
        return;
      }
      if (path.startsWith('/governance/models/') && req.method === 'PATCH') {
        const ref = decodeURIComponent(path.slice('/governance/models/'.length));
        const row = modelRows.find((m) => m.ref === ref);
        if (!row) {
          res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'NOT_FOUND', message: `mock: no model ${ref}`, details: {} }));
          return;
        }
        const patchable = ['capabilities', 'unitPriceInputPer1k', 'unitPriceOutputPer1k', 'applicableAgents', 'enabled', 'endpoint', 'apiKeySecretRef'];
        const changed = Object.fromEntries(Object.entries(echo ?? {}).filter(([key, value]) => patchable.includes(key) && value !== null));
        if (!Object.keys(changed).length) {
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'VALIDATION_ERROR', message: 'mock: at least one field must be supplied to update a model', details: {} }));
          return;
        }
        const { capabilities, ...rest } = changed;
        Object.assign(row, rest);
        if (capabilities) row.capabilities = { ...row.capabilities, ...capabilities };
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(row));
        return;
      }
      // CP-IAM-005 ①: the same floor S1's service applies to a create, not only to a reset — an
      // account born with a 1-character password is the bypass this round closed. The answer is
      // built field by field because the request body carries the password, and an `echo` of it
      // would put a credential in a response the page can read.
      if (path === '/users' && req.method === 'POST') {
        if (!authOn) {
          res.writeHead(403, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'PERMISSION_DENIED', message: 'mock: no user.manage without an account deployment', details: {} }));
          return;
        }
        const supplied = String(echo?.password ?? '');
        if (supplied && supplied.length < 8) {
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({
            errorCode: 'VALIDATION_ERROR',
            message: 'mock: a password must be at least 8 characters',
            details: { fields: [{ path: 'password', message: 'too short' }] },
          }));
          return;
        }
        const row = {
          id: `usr-mock-${createdUsers.length + 1}`,
          subject: String(echo?.subject ?? ''),
          displayName: String(echo?.displayName ?? ''),
          email: String(echo?.email ?? ''),
          source: echo?.source === 'oidc' ? 'oidc' : 'local',
          status: 'ACTIVE',
          locked: false,
          mustChangePassword: echo?.mustChangePassword === true,
        };
        createdUsers.push(row);
        res.writeHead(201, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(row));
        return;
      }
      // S1's `PATCH /users/{id}`: display name and email are corrected after the fact, and `status`
      // is the *only* way to retire an account (there is no delete). Disabling revokes that account's
      // sessions, so a harness that left the operator signed in would be describing a server that
      // does not exist.
      if (/^\/users\/[^/]+$/.test(path) && req.method === 'PATCH') {
        const id = decodeURIComponent(path.slice('/users/'.length));
        const row = createdUsers.find((u) => u.id === id) ?? LOCK_ROWS.find((u) => u.id === id);
        if (!row) {
          res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'NOT_FOUND', message: `mock: no account ${id}`, details: {} }));
          return;
        }
        if (echo?.displayName !== undefined) row.displayName = String(echo.displayName);
        if (echo?.email !== undefined) row.email = String(echo.email);
        if (echo?.status !== undefined) {
          row.status = String(echo.status);
          if (row.status === 'DISABLED' && row.subject === ACCOUNT.subject) signedInNow = false;
        }
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(row));
        return;
      }
      // Role grants, the two halves the account screen offers. S1's revoke *deletes* the row, so a
      // revoked role stops appearing rather than appearing with a flag — the harness has to behave
      // the same or the console can pass on a list the real server would have shrunk.
      if (/^\/users\/[^/]+\/roles$/.test(path) && (req.method === 'PUT' || req.method === 'DELETE')) {
        const id = decodeURIComponent(path.split('/')[2]);
        const row = createdUsers.find((u) => u.id === id) ?? LOCK_ROWS.find((u) => u.id === id);
        const wantedRole = String(echo?.role ?? url.searchParams.get('role') ?? '');
        const wantedProject = echo?.projectId ?? url.searchParams.get('projectId') ?? null;
        if (!row) {
          res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'NOT_FOUND', message: `mock: no account ${id}`, details: {} }));
          return;
        }
        if (!wantedRole) {
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({
            errorCode: 'VALIDATION_ERROR',
            message: 'mock: a role is required',
            details: { fields: [{ path: 'role', message: 'required' }] },
          }));
          return;
        }
        if (req.method === 'PUT') {
          row.roles = [...(row.roles ?? []).filter((r) => r.role !== wantedRole || (r.projectId ?? null) !== wantedProject),
            { role: wantedRole, projectId: wantedProject, revoked: null }];
          res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ userId: id, role: wantedRole, projectId: wantedProject, revoked: false }));
          return;
        }
        row.roles = (row.roles ?? []).filter((r) => r.role !== wantedRole || (r.projectId ?? null) !== wantedProject);
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ userId: id, role: wantedRole, projectId: wantedProject, revoked: true }));
        return;
      }
      // CP-IAM-004: writing a password revokes every session of the account, including the one that
      // asked. A harness that let the session survive would let the app pass a test on a state the
      // real server never produces.
      if (/^\/users\/[^/]+\/password$/.test(path)) {
        const supplied = String(echo?.password ?? '');
        if (supplied.length < 8) {
          // CP-IAM-005 ①: the floor is the service's rule, and it names the field without the value
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({
            errorCode: 'VALIDATION_ERROR',
            message: 'mock: a password must be at least 8 characters',
            details: { fields: [{ path: 'password', message: 'too short' }] },
          }));
          return;
        }
        // CP-IAM-004: the write revoked every session of this account, so there is nothing to
        // come back to — the next `/auth/session` has to say so, and reads must stop working.
        signedInNow = false;
        lapsed = false;
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ cleared: true }));
        return;
      }
      // ---- M4 writes (CP-UI-023). Each mutates the live rows above and answers with the projection
      // S1 answers with, so the screen's re-read shows the write rather than the fixture.
      //
      // UI-PRJ-004: management attributes. An omitted field means "leave it alone", exactly as
      // UpdateProjectBody declares, so the live row is merged rather than replaced.
      if (path === `/projects/${P}` && req.method === 'PATCH') {
        if (echo && typeof echo === 'object') Object.assign(projectRow, echo);
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(projectRow));
        return;
      }
      //
      // Membership is a *set*: `PUT /projects/{id}/members` carries the whole list, rows not in it
      // are removed. Answering an echo here would let a page that dropped an untouched row pass.
      if (path === `/projects/${P}/members` && req.method === 'PUT') {
        const wanted = Array.isArray(echo?.members) ? echo.members : null;
        if (!wanted) {
          badField(res, cors, 'members', 'the whole membership list is required');
          return;
        }
        const next = [];
        for (const item of wanted) {
          if (!item?.userId) continue;
          const account = ACCOUNT_ROWS().find((u) => u.id === item.userId);
          next.push({
            id: `mbr-${next.length + 1}`,
            projectId: P,
            userId: String(item.userId),
            // S1 resolves the subject from the account table, not from the request body.
            subject: account?.subject ?? String(item.subject ?? ''),
            role: item.role ?? null,
          });
        }
        memberRows.splice(0, memberRows.length, ...next);
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(envelope(memberRows)));
        return;
      }
      const memberOne = path.match(/^\/projects\/([^/]+)\/members\/([^/]+)$/);
      if (memberOne && req.method === 'DELETE') {
        const userId = decodeURIComponent(memberOne[2]);
        const at = memberRows.findIndex((r) => r.userId === userId);
        // `service.remove_member` raises NotFoundControlError for a person who is not a member, so a
        // harness that answered `cleared` anyway would hide a remove of nothing as a success.
        if (at < 0) {
          res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify(notFoundBody(`user ${userId} is not a member`, { userId })));
          return;
        }
        memberRows.splice(at, 1);
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ cleared: true, userId, projectId: P }));
        return;
      }
      if (path === `/projects/${P}/artifact-grants` && req.method === 'POST') {
        const classification = String(echo?.classification ?? '');
        const userId = String(echo?.userId ?? '');
        if (!userId) {
          badField(res, cors, 'userId', 'required');
          return;
        }
        if (!GRANT_CLASSIFICATIONS.includes(classification)) {
          badField(res, cors, 'classification', `classification is one of ${GRANT_CLASSIFICATIONS.join(', ')}`);
          return;
        }
        if (!(String(echo?.reason ?? '')).trim()) {
          badField(res, cors, 'reason', 'a restricted read grant requires a reason');
          return;
        }
        const account = ACCOUNT_ROWS().find((u) => u.id === userId);
        if (!account) {
          res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify(notFoundBody(`no account ${userId}`, { userId })));
          return;
        }
        // `grant_restricted` calls `_ensure_membership` first: authorising a stranger makes them a
        // member, and the members table has to show that.
        if (!memberRows.some((r) => r.userId === userId)) {
          memberRows.push({ id: `mbr-${memberRows.length + 1}`, projectId: P, userId, subject: account.subject, role: null });
        }
        // One row per (account, classification): a re-grant replaces the reason and the window rather
        // than adding a second authorisation for the same pair.
        const existing = grantRows.find((r) => r.userId === userId && r.classification === classification);
        const row = existing ?? { id: `rgt-${grantRows.length + 1}`, projectId: P, userId, subject: account.subject, classification };
        row.reason = String(echo.reason).trim();
        row.grantedBy = req.headers['x-operator'] ? decodeURIComponent(String(req.headers['x-operator'])) : 'tester';
        row.expiresAt = echo?.expiresAt ?? null;
        row.revoked = false;
        if (!existing) grantRows.push(row);
        res.writeHead(201, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(row));
        return;
      }
      const grantOne = path.match(/^\/projects\/([^/]+)\/artifact-grants\/([^/]+)$/);
      if (grantOne && req.method === 'DELETE') {
        const userId = decodeURIComponent(grantOne[2]);
        const classification = url.searchParams.get('classification');
        // `classification` is a required query parameter on the route, and the row is keyed on the
        // pair: without it the harness would revoke a grant the operator did not name.
        if (!classification) {
          badField(res, cors, 'classification', 'required');
          return;
        }
        const at = grantRows.findIndex((r) => r.userId === userId && r.classification === classification);
        if (at >= 0) grantRows[at].revoked = true;
        // `revoke_restricted` answers `cleared` either way and keeps the row for the audit trail.
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ cleared: true, userId, projectId: P, classification }));
        return;
      }
      if (path === `/projects/${P}/production-exemptions` && req.method === 'POST') {
        const reason = String(echo?.reason ?? '');
        const waiver = echo?.waiver === undefined ? EXEMPTION_WAIVERS[0] : String(echo.waiver);
        if (!reason.trim()) {
          badField(res, cors, 'reason', 'an exemption requires a reason');
          return;
        }
        if (!EXEMPTION_WAIVERS.includes(waiver)) {
          badField(res, cors, 'waiver', `waiver is one of ${EXEMPTION_WAIVERS.join(', ')}`);
          return;
        }
        const row = {
          id: `pex-${exemptionRows.length + 1}`,
          projectId: P,
          environmentKey: String(echo?.environmentKey ?? ''),
          waiver,
          reason: reason.trim(),
          grantedBy: req.headers['x-operator'] ? decodeURIComponent(String(req.headers['x-operator'])) : 'tester',
          // `grant_exemption` gives a waiver that sets no window its own default expiry, so an empty
          // field must not read back as "never expires".
          expiresAt: echo?.expiresAt ?? new Date(Date.now() + 7 * 86_400_000).toISOString(),
          revoked: false,
        };
        exemptionRows.push(row);
        res.writeHead(201, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(row));
        return;
      }
      const exemptionOne = path.match(/^\/production-exemptions\/([^/]+)$/);
      if (exemptionOne && req.method === 'DELETE') {
        const id = decodeURIComponent(exemptionOne[1]);
        const row = exemptionRows.find((r) => r.id === id);
        if (!row) {
          res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify(notFoundBody(`exemption ${id} not found`, { exemptionId: id })));
          return;
        }
        row.revoked = true;
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ cleared: true, projectId: row.projectId, waiver: row.waiver }));
        return;
      }
      // S5IF-SEC01: a credential write answers with the reference and the timestamps, and keeps no
      // value — the same promise the real store makes. `path` has `/api/v1` stripped at the top of
      // this handler, so a prefix written with the prefix in it never matched and every credential
      // write fell through to the echo below — which answered with the submitted value, the one
      // thing this endpoint is not allowed to do.
      if (path.startsWith('/secrets/') && (req.method === 'PUT' || req.method === 'POST')) {
        const name = decodeURIComponent(path.slice('/secrets/'.length));
        const previous = secretStore.get(name);
        secretStore.set(name, {
          name,
          ref: `db://${name}`,
          algo: 'aes-256-gcm+hkdf-sha256',
          createdAt: previous?.createdAt ?? new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          rotatedAt: previous ? new Date().toISOString() : null,
          createdBy: req.headers['x-operator'] ? decodeURIComponent(String(req.headers['x-operator'])) : 'tester',
        });
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(secretStore.get(name)));
        return;
      }
      if (path.startsWith('/secrets/') && req.method === 'DELETE') {
        const name = decodeURIComponent(path.slice('/secrets/'.length));
        const gone = secretStore.delete(name);
        res.writeHead(gone ? 200 : 404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify(gone ? { name, deleted: true } : { errorCode: 'NOT_FOUND', message: 'mock', details: {} }));
        return;
      }
      // FR-DATA-008: purge clears the bytes and keeps the artifact row. X-Operator is required by S1
      // (CP-API-002) and nothing else — no body, no idempotency key (the route declares neither).
      if (path === '/artifacts/art-openapi/purge' && req.method === 'POST') {
        const operator = req.headers['x-operator'];
        if (!operator) {
          res.writeHead(400, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'OPERATOR_REQUIRED', message: 'mock: a state change needs X-Operator', details: { header: 'X-Operator' } }));
          return;
        }
        purgedArtifacts.add('art-openapi');
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ artifactId: 'art-openapi', contentPurged: true }));
        return;
      }
      // CP-IAM-004, mirrored. S1 answers a cookie-authenticated change that does not name the CSRF
      // value with 403 (`rest/middleware.py:438-448`). This harness accepted every unmatched POST with
      // `{accepted:true}` instead, which is how an upload that never carried the header — the defect
      // this file's own page was written around — looked like a working feature for as long as the
      // harness has existed. Only under the `authOn` lever, so the specs that never sign in are
      // unaffected.
      if (authOn && signedInNow && !path.startsWith('/auth/') && !req.headers['x-csrf-token']) {
        res.writeHead(403, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ errorCode: 'CSRF_FAILED', message: 'mock: a cookie-authenticated change must echo X-CSRF-Token', details: { header: 'X-CSRF-Token' } }));
        return;
      }
      // S5IF-FLD11: a hand-authored patch. S2 reads RFC 6902 operations and refuses a body without
      // them (`adapters/registry.py`), so the harness refuses it the same way rather than accepting
      // the `{ <section>: model }` shape the console used to send. Answers are the three real ones:
      // PROPOSED, REJECTED with issues (duplicate businessKey), VALIDATION_ERROR.
      const patchMatch = path.match(/^\/stages\/([^/]+)\/patches$/);
      if (patchMatch && req.method === 'POST') {
        const operations = Array.isArray(echo?.patch?.operations) ? echo.patch.operations : Array.isArray(echo?.operations) ? echo.operations : null;
        if (!operations?.length) {
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            errorCode: 'VALIDATION_ERROR',
            message: 'mock: a manual patch must carry RFC 6902 operations',
            details: { fields: [{ path: 'operations', message: 'must be a non-empty list' }] },
          }));
          return;
        }
        // Only the shapes the entry page emits: a guarded whole-container replace, and an append.
        const nextItems = [];
        let rejected = null;
        let stale = null;
        for (const op of operations) {
          if (op.op === 'test') {
            // S2 applies a `test` literally and aborts the whole patch when it fails
            // (`patch/rfc6902.py` → BP_PATCH_MALFORMED → 422 SCHEMA_ERROR), which is how the entry
            // page learns the draft moved underneath it. Mirrored here so the guard is testable.
            const path = String(op.path ?? '');
            let actual;
            if (path === '/requirements/items') actual = requirementRows;
            else {
              const m = /^\/requirements\/items\/(\d+|-)\/([^/]+)$/.exec(path);
              const index = m ? (m[1] === '-' ? requirementRows.length - 1 : Number(m[1])) : -1;
              actual = m && requirementRows[index] ? requirementRows[index][m[2]] : undefined;
            }
            if (JSON.stringify(actual ?? null) !== JSON.stringify(op.value ?? null)) {
              stale = `mock: operation test failed at ${path}`;
              break;
            }
            continue;
          }
          if (op.path === '/requirements/items' && op.op === 'replace') nextItems.push(...(Array.isArray(op.value) ? op.value : []));
          else if (op.path === '/requirements/items/-' && op.op === 'add') nextItems.push(op.value);
          else if (op.path === '/requirements/items' && op.op === 'add') nextItems.push(...(Array.isArray(op.value) ? op.value : []));
        }
        if (stale) {
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ errorCode: 'SCHEMA_ERROR', message: stale, details: { providerErrorCode: 'BP_PATCH_MALFORMED' } }));
          return;
        }
        const keys = nextItems.map((i) => String(i?.businessKey ?? ''));
        if (keys.some((k, i) => keys.indexOf(k) !== i)) {
          rejected = {
            issueId: 'mock-keys-1', ruleId: 'BPV-PCH-003', level: 'BLOCKING',
            description: 'mock: one businessKey may name only one entry in a section',
            businessKey: keys.find((k, i) => keys.indexOf(k) !== i), suggestedAction: '换一个业务标识',
          };
        }
        if (rejected) {
          res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            stageId: decodeURIComponent(patchMatch[1]), patchStatus: 'REJECTED',
            draftHash: `sha256:mock-${Date.now()}`, issues: [rejected], questions: [], gateInvalidated: false, assignedUids: {},
          }));
          return;
        }
        requirementRows.length = 0;
        requirementRows.push(...nextItems.sort((a, b) => (String(a.businessKey) < String(b.businessKey) ? -1 : 1)));
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          stageId: decodeURIComponent(patchMatch[1]), patchStatus: 'PROPOSED',
          draftHash: `sha256:mock-${requirementRows.length}-${Date.now()}`, issues: [], questions: [], gateInvalidated: false, assignedUids: {},
        }));
        return;
      }
      // UI-REQS-001: the upload the operator just made has to arrive in the material list, so the
      // harness keeps the row it accepted instead of answering 201 about nothing.
      if (path === `/projects/${P}/inputs` && req.method === 'POST') {
        const raw = String(echo?.raw ?? '');
        const filename = raw.match(/filename="([^"]*)"/)?.[1] ?? 'uploaded.bin';
        const row = {
          artifactId: `in-mock-${uploadedInputs.length + 1}`,
          filename,
          mediaType: 'application/octet-stream',
          sizeBytes: Math.max(1, raw.length),
          parseStatus: 'NONE',
          dataClassification: String(echo?.dataClassification ?? 'INTERNAL'),
          contentHash: `sha256:mock-upload-${uploadedInputs.length + 1}`,
          createdAt: new Date().toISOString(),
        };
        uploadedInputs.unshift(row);
        res.writeHead(201, { ...cors, 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ...row, artifactId: row.artifactId }));
        return;
      }
      // UI-PRJ-004 / CP-PRJ-009: restoring a project from an exported artifact. A 202 whose
      // `stepRunId` is the new project's id, and the new row really does arrive in `GET /projects` —
      // otherwise the success message names a project the list then refuses to show.
      if (path === '/projects/import' && req.method === 'POST') {
        if (!req.headers['x-operator']) {
          res.writeHead(400, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'OPERATOR_REQUIRED', message: 'mock: a state change needs X-Operator', details: { header: 'X-Operator' } }));
          return;
        }
        const artifactId = String(echo?.artifactId ?? '');
        if (!artifactId) {
          res.writeHead(422, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
          res.end(JSON.stringify({ errorCode: 'VALIDATION_FAILED', message: 'mock: artifactId is required', details: { field: 'artifactId' } }));
          return;
        }
        const restored = { id: `proj-imported-${importedProjects.length + 1}`, name: `导入的 ${artifactId}`, key: 'imported', description: 'mock', status: 'ACTIVE', deliveryMode: 'A', archived: false };
        importedProjects.push(restored);
        res.writeHead(202, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({ stepRunId: restored.id, accepted: true }));
        return;
      }
      // CP-Q-005: one questionnaire submit. The harness records the answer on the question it
      // names and hands back the still-open rows, so the review page can render a partial answer
      // and the next round the way S1 does.
      if (path === '/questions/answers' && req.method === 'POST') {
        const submitted = Array.isArray(echo?.answers) ? echo.answers : [];
        const answered = submitted.map((answer) => {
          const question = mockQuestions.find((row) => row.id === answer.questionId);
          if (!question) return { questionId: answer.questionId, status: 'ANSWERED' };
          const ids = Array.isArray(answer.suggestionIds) ? answer.suggestionIds : [];
          const chosen = (question.suggestions ?? []).filter((suggestion) => ids.includes(suggestion.id));
          const text = [...chosen.map((suggestion) => String(suggestion.text ?? '')), String(answer.customText ?? '')]
            .filter(Boolean)
            .join('; ');
          question.status = 'ANSWERED';
          question.answer = {
            suggestionId: chosen[0]?.id ?? null,
            suggestionIds: chosen.map((suggestion) => suggestion.id),
            suggestionTexts: chosen.map((suggestion) => String(suggestion.text ?? '')),
            customText: answer.customText ?? null,
            text,
            answeredAt: new Date().toISOString(),
          };
          return { questionId: question.id, status: 'ANSWERED' };
        });
        const open = mockQuestions.filter((question) => question.status === 'OPEN');
        res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
        res.end(JSON.stringify({
          answers: answered,
          resumed: false,
          runId: 'AR-1',
          status: 'WAITING_INPUT',
          questions: open,
        }));
        return;
      }
      res.writeHead(200, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
      res.end(JSON.stringify({ accepted: true, echo }));
    });
    return;
  }

  // Reads follow the same refusal as writes: the app learns the session is gone from a 401, which is
  // the only signal UM-NET-009's watch is built on. `/auth/session` must keep answering 200 — S1
  // answers it before anyone is signed in, and the app uses it to find out where it stands.
  if (authOn && lapsed && !path.startsWith('/auth/')) {
    res.writeHead(401, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
    res.end(JSON.stringify({ errorCode: 'UNAUTHENTICATED', message: 'mock: session lapsed', details: {} }));
    return;
  }

  // CP-UI-021: one file of the development package. Its path carries slashes, so it cannot live in
  // the exact-match table above; answering it as plain text is what makes the app's `parseAs: 'text'`
  // read the same shape S1 sends.
  const hfFile = path.match(new RegExp(`^/projects/${P}/frontend-package/files/(.+)$`));
  if (hfFile) {
    const wanted = decodeURIComponent(hfFile[1] ?? '');
    const text = FRONTEND_PACKAGE_FILES[wanted];
    if (text === undefined) {
      res.writeHead(404, { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
      res.end(JSON.stringify({ errorCode: 'NOT_FOUND', message: 'mock: not in the listing', details: { reason: 'NOT_EXIST' } }));
      return;
    }
    res.writeHead(200, { ...cors, 'Content-Type': 'text/plain; charset=utf-8', 'X-Correlation-Id': 'mock' });
    res.end(text);
    return;
  }

  const hit = routes[`GET ${path}`];
  // S1 answers an error body with the HTTP status its code maps to (SRS §8.3). A 200 carrying
  // `errorCode` makes the client's error mapping unreachable, which is exactly what UM-AC-19
  // depends on: the graph's 404 is what becomes the guided empty state.
  const out = hit ? hit(url.searchParams) : envelope([]);
  res.writeHead(httpStatusOf(out), { ...cors, 'Content-Type': 'application/json', 'X-Correlation-Id': 'mock' });
  res.end(typeof out === 'string' ? JSON.stringify({ content: out }) : JSON.stringify(out));
});

process.on('uncaughtException', (e) => console.error('mock recovered from', e?.message ?? e));
process.on('unhandledRejection', (e) => console.error('mock recovered from', e?.message ?? e));

server.listen(PORT, '127.0.0.1', () => {
  console.log(`mock S1 on http://127.0.0.1:${PORT}/api/v1 (SSE ${sseOff ? 'off' : 'on'}, derived ${uncompiled ? 'not generated' : 'ready'})`);
});
