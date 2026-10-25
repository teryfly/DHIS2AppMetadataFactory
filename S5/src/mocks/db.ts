/*
 * UI-NFR-05: an in-memory mock keyed to the S1 OpenAPI shapes so S5 is developed and component-
 * tested without a live S1. Bodies are camelCase and match the vendored contract; nothing here is
 * "business logic" — it is fixed fixture data. Screens read it through TanStack exactly as they
 * would read S1.
 */
import type {
  ProjectView,
  WorkflowView,
  StageView,
  AllowedActionsView,
  GateView,
  QuestionView,
  EnvironmentView,
  PolicyView,
  DecisionList,
  NotificationPage,
  RegistryPage,
  InputPage,
  RunView,
  SetupView,
  ModelView,
  AgentSpecView,
} from '@/api/types';

const now = () => new Date().toISOString();

export const WORKFLOW_ID = 'WF-9a1';
export const DESIGN_PROJECT_ID = 'PRJ-green-01';
export const HANDOFF_PROJECT_ID = 'PRJ-brown-02';

function stage(
  id: string,
  stageType: string,
  status: string,
  extra: Partial<StageView> = {},
): StageView {
  return {
    id,
    stageType,
    status,
    attempt: 0,
    artifactCount: 0,
    openQuestionCount: 0,
    highOpenQuestionCount: 0,
    blockingIssueCount: 0,
    ...extra,
  };
}

// --- project 1: mode A / greenfield, mid-flow at REQUIREMENTS gate ---
const STAGES_P1: StageView[] = [
  stage('ST-req', 'REQUIREMENTS', 'WAITING_APPROVAL', {
    artifactCount: 4,
    openQuestionCount: 1,
    highOpenQuestionCount: 1,
    currentArtifactVersion: '3',
    lastRunAt: now(),
  }),
  stage('ST-domain', 'DOMAIN', 'PENDING'),
  stage('ST-disc', 'DISCOVERY', 'PENDING'),
  stage('ST-map', 'MAPPING', 'PENDING'),
  stage('ST-page', 'PAGE_DESIGN', 'SKIPPED'),
  stage('ST-build', 'BASELINE_COMPILE', 'PENDING'),
  stage('ST-deploy', 'DHIS2_VALIDATE', 'PENDING'),
  stage('ST-handoff', 'HANDOFF', 'SKIPPED'),
];

export const workflow1: WorkflowView = {
  id: WORKFLOW_ID,
  projectId: DESIGN_PROJECT_ID,
  status: 'IN_PROGRESS',
  paused: false,
  cancelled: false,
  version: 7,
  lastEventSeq: 42,
  currentStageId: 'ST-req',
  stages: STAGES_P1,
  deploymentRuns: [],
  activeDraft: { draftId: 'DRF-1', contentHash: 'sha256:ab12', baseVersion: null, stage: 'REQUIREMENTS', stageScope: ['REQUIREMENTS'] },
};

export const project1: ProjectView = {
  id: DESIGN_PROJECT_ID,
  name: 'Community Health Register',
  description: 'Greenfield register for CHW service delivery.',
  deliveryMode: 'A',
  projectType: 'GREENFIELD',
  ownerName: 'Ana Ruiz',
  defaultLanguage: 'zh-CN',
  translationLanguages: ['en'],
  versionConstraints: '2.40',
  workflowId: WORKFLOW_ID,
  workflowStatus: 'IN_PROGRESS',
  currentStageId: 'ST-req',
  currentBlueprintVersion: null,
  milestoneStatus: 'M1',
  archived: false,
  stages: STAGES_P1.map((s) => ({ id: s.id, stageType: s.stageType, status: s.status, attempt: s.attempt })),
  targetEnvironments: [
    { environmentKey: 'env-dev', isDesignReference: true, promotionOrder: 1, environmentClass: 'TEST' },
    { environmentKey: 'env-stg', isDesignReference: false, promotionOrder: 2, environmentClass: 'STAGING' },
    { environmentKey: 'env-prod', isDesignReference: false, promotionOrder: 3, environmentClass: 'PRODUCTION' },
  ],
};

// --- project 2: mode B / brownfield, handoff path (M3, shown as such) ---
const STAGES_P2 = [
  stage('ST2-req', 'REQUIREMENTS', 'PASSED', { artifactCount: 6, completedAt: now() }),
  stage('ST2-domain', 'DOMAIN', 'PASSED', { artifactCount: 3 }),
  stage('ST2-disc', 'DISCOVERY', 'RUNNING', { attempt: 1, lastRunAt: now() }),
  stage('ST2-map', 'MAPPING', 'PENDING'),
  stage('ST2-page', 'PAGE_DESIGN', 'PENDING'),
  stage('ST2-build', 'BASELINE_COMPILE', 'PENDING'),
  stage('ST2-deploy', 'DHIS2_VALIDATE', 'PENDING'),
  stage('ST2-handoff', 'HANDOFF', 'PENDING'),
];
export const project2: ProjectView = {
  id: HANDOFF_PROJECT_ID,
  name: 'ANC Tally Migration',
  description: 'Brownfield refactor of an existing ANC tally app.',
  deliveryMode: 'B',
  projectType: 'BROWNFIELD',
  ownerName: 'Kwame Boateng',
  defaultLanguage: 'en',
  translationLanguages: ['fr'],
  workflowId: 'WF-b2',
  workflowStatus: 'IN_PROGRESS',
  milestoneStatus: 'M2',
  archived: false,
  stages: STAGES_P2.map((s) => ({ id: s.id, stageType: s.stageType, status: s.status, attempt: s.attempt })),
  targetEnvironments: [{ environmentKey: 'env-stg', isDesignReference: true, promotionOrder: 1, environmentClass: 'STAGING' }],
};

export const projects: ProjectView[] = [project1, project2];

/** GET /allowed-actions: the Administration pages' answer (S1 emits scope 'platform'). */
export const platformActions: AllowedActionsView = {
  workflowId: null,
  scope: 'platform',
  evaluatedAt: now(),
  allowed: [
    // S1 1.6.0 puts `project.create` in the platform list because `POST /projects` guards itself
    // with that name; the mock has to answer it too, or the wizard's submit would be gated on an
    // entry a real S1 sends (UI-BR-01: a missing entry means unavailable).
    { action: 'project.create', available: true },
    { action: 'environment.create', available: true },
    { action: 'secret.manage', available: true },
    { action: 'environment.edit', available: true, targetId: 'env-dev' },
    { action: 'environment.test_connection', available: true, targetId: 'env-dev' },
    { action: 'environment.emergency_lock', available: true, targetId: 'env-dev', details: { locked: false } },
    { action: 'environment.disable', available: true, targetId: 'env-dev' },
    { action: 'environment.probe_capabilities', available: true, targetId: 'env-dev' },
    // `env-stg` is the disabled one: S1 answers every environment action for it, and
    // `platformActionsView` refuses the three that touch the instance — so the console shows the
    // split instead of guessing it from the row.
    { action: 'environment.edit', available: true, targetId: 'env-stg' },
    { action: 'environment.test_connection', available: true, targetId: 'env-stg' },
    { action: 'environment.emergency_lock', available: true, targetId: 'env-stg', details: { locked: false } },
    { action: 'environment.disable', available: true, targetId: 'env-stg' },
    { action: 'environment.probe_capabilities', available: true, targetId: 'env-stg' },
    { action: 'environment.edit', available: true, targetId: 'env-prod' },
    { action: 'environment.test_connection', available: true, targetId: 'env-prod' },
    { action: 'environment.emergency_lock', available: true, targetId: 'env-prod', details: { locked: true } },
    { action: 'environment.disable', available: true, targetId: 'env-prod' },
    { action: 'environment.probe_capabilities', available: true, targetId: 'env-prod' },
    { action: 'governance.provider.register', available: true },
    { action: 'governance.provider.enable', available: true },
    { action: 'governance.model.register', available: true },
    { action: 'governance.model.enable', available: true },
    { action: 'governance.prompt.activate', available: true },
    { action: 'governance.policy.relax', available: true },
    // AI-GOV-007's write side, delivered with M4 (UI-ADM-005).
    { action: 'governance.budget.set', available: true },
    { action: 'user.manage', available: true },
    // The same three rights the project screens read from the workflow answer. A platform
    // administrator holds them globally, so this list offers them; the per-project refusal
    // `admin.test.tsx` pins is in `allowedActions1`, which is where those three screens look.
    { action: 'member.manage', available: true },
    { action: 'restricted_grant.manage', available: true },
    { action: 'production_exemption.manage', available: true },
    // `PUT /settings` authorizes this one; the console's save button is gated on it (UI-BR-01),
    // so the mock has to answer it or the settings screen can never be exercised at all.
    { action: 'settings.manage', available: true },
  ],
  unavailable: [],
};

// --- allowed actions for WF-1 (single source of button availability) ---
export const allowedActions1: AllowedActionsView = {
  workflowId: WORKFLOW_ID,
  scope: 'workflow',
  evaluatedAt: now(),
  allowed: [
    { action: 'patch.submit', available: true, targetId: 'ST-req' },
    { action: 'question.answer', available: true, targetId: 'ST-req' },
    { action: 'stage.run', available: true, targetId: 'ST-domain' },
    { action: 'stage.rerun', available: true, targetId: 'ST-req' },
    // The three Administration rights the project's own screens use. They are answered here — with
    // this project's id filtering them — and *also* in the platform list, because a role can be
    // granted for one project alone (CP-IAM-002): asking the platform answer would tell a project's
    // own member manager they may not manage it. The names are S1's own (`iam.py:91-93`); the
    // earlier `project.member.manage` / `artifact.grant.manage` / `production.exemption.manage`
    // spellings matched nothing the server ever sends, which is what made these buttons grey.
    { action: 'member.manage', available: true },
    { action: 'restricted_grant.manage', available: true },
    { action: 'production_exemption.manage', available: false, reasonCode: 'PERMISSION_DENIED' },
    // AI-CHAT-001: in-stage dialogue exists only for the four dialogue stage types, and not while a
    // stage is LOCKED or SKIPPED — the same two sets S1 checks, so a control is never offered for a
    // turn the service would refuse (S5IF-G10).
    { action: 'stage.chat', available: true, targetId: 'ST-req' },
    { action: 'stage.chat', available: true, targetId: 'ST-domain' },
    {
      action: 'stage.chat',
      available: false,
      targetId: 'ST-disc',
      reasonCode: 'STAGE_NOT_READY',
      details: { stageType: 'DISCOVERY', chatable: ['DOMAIN', 'MAPPING', 'PAGE_DESIGN', 'REQUIREMENTS'] },
    },
    {
      action: 'stage.chat',
      available: false,
      targetId: 'ST-page',
      reasonCode: 'STAGE_NOT_READY',
      details: { status: 'SKIPPED' },
    },
    { action: 'workflow.pause', available: true },
    { action: 'workflow.cancel', available: true },
    { action: 'environment.create', available: true },
    { action: 'input.upload', available: true },
    { action: 'input.parse', available: true },
    { action: 'input.confirm_column_mapping', available: true },
    // S1 lists these as forwarding actions (domain/actions.py FORWARDING_ACTIONS); the mock has
    // to carry them or every screen that gates on them looks unavailable in development too.
    { action: 'artifact.explain', available: true },
    { action: 'artifact.download', available: true },
    { action: 'agent.cancel', available: true },
    // run-scoped: S1 answers it per run id, so the console's cancel is gated on the row it shows
    { action: 'deployment.cancel', available: true, targetId: 'RUN-1' },
    { action: 'registry_binding.set', available: true },
    // per change request, exactly as S1 derives it from CP-CR-002's transitions:
    // CR-1 is ANALYZED (decide), CR-2 is APPROVED (start)
    { action: 'change_request.impact', available: true, targetId: 'CR-1' },
    { action: 'change_request.approve', available: true, targetId: 'CR-1' },
    { action: 'change_request.reject', available: true, targetId: 'CR-1' },
    { action: 'change_request.start', available: false, targetId: 'CR-1', reasonCode: 'STATE_NOT_ALLOWED', details: { status: 'ANALYZED', wouldBecome: 'IN_PROGRESS' } },
    { action: 'change_request.start', available: true, targetId: 'CR-2' },
    { action: 'change_request.complete', available: false, targetId: 'CR-2', reasonCode: 'STATE_NOT_ALLOWED', details: { status: 'APPROVED', wouldBecome: 'COMPLETED' } },
    { action: 'project.export', available: true },
    { action: 'gate.reject', available: true, targetId: 'GT-1' },
  ],
  unavailable: [
    { action: 'gate.approve', available: false, targetId: 'GT-1', reasonCode: 'WARNING_UNRESOLVED' },
    { action: 'stage.submit_for_approval', available: false, targetId: 'ST-req', reasonCode: 'HIGH_QUESTION_OPEN' },
    { action: 'stage.resolve_manual_check', available: false, targetId: 'ST-req', reasonCode: 'STAGE_NOT_IN_MANUAL_CHECK' },
    { action: 'execution.commit', available: false, targetId: 'RUN-1', reasonCode: 'GATE_NOT_APPROVED' },
    { action: 'workflow.resume', available: false, reasonCode: 'WORKFLOW_PAUSED' },
    { action: 'deployment.create', available: false, targetId: 'env-prod', reasonCode: 'DESIGN_LINE_INCOMPLETE' },
  ],
};

// --- gate for the requirements stage ---
export const gate1: GateView = {
  id: 'GT-1',
  type: 'REQUIREMENTS_GATE',
  status: 'WAITING_APPROVAL',
  workflowId: WORKFLOW_ID,
  stageId: 'ST-req',
  requiredApprovals: 1,
  approvals: [],
  boundObjects: [
    { refType: 'entity', refId: 'entity:chw', contentHash: 'sha256:h-1' },
    { refType: 'rule', refId: 'rule:age-range', contentHash: 'sha256:h-2' },
  ],
  warnings: [
    { level: 'WARNING', message: 'Use case “Register visit” has no confirmed actor mapping.', ruleId: 'R-101', businessKey: 'uc:register-visit' },
    { level: 'WARNING', message: 'Low confidence (0.55) on entity “Facility”.', ruleId: 'R-220', businessKey: 'entity:facility' },
  ],
  confirmationItems: [],
};

// --- questions ---
export const question1: QuestionView = {
  id: 'Q-1',
  text: 'Should the Facility entity reuse the existing DHIS2 OrgUnit, or a new tracked entity?',
  severity: 'HIGH',
  answerMode: 'SINGLE',
  status: 'OPEN',
  blocking: true,
  workflowId: WORKFLOW_ID,
  stageId: 'ST-req',
  affects: ['entity:facility'],
  source: { agentRunId: 'AR-1', kind: 'agent', localId: '1' },
  suggestions: [
    { id: 'S-1', text: 'Reuse OrgUnit (no new UID)', rationale: 'Avoids duplicating an authoritative source.', recommended: true, changesUid: false, regenerates: ['entity:facility'] },
    { id: 'S-2', text: 'Create a tracked entity type', rationale: 'Enables program enrollment.', recommended: false, changesUid: true, regenerates: ['entity:facility', 'rule:enroll'] },
  ],
};

export const question2: QuestionView = {
  id: 'Q-2',
  text: 'Confirm the age range lower bound for the eligibility rule.',
  severity: 'MEDIUM',
  answerMode: 'SINGLE',
  status: 'ANSWERED',
  blocking: false,
  workflowId: WORKFLOW_ID,
  stageId: 'ST-req',
  affects: ['rule:age-range'],
  answeredBy: 'Ana Ruiz',
  answeredAt: now(),
  answer: { text: 'Lower bound is 15 years.', suggestionId: 'S-9', suggestionText: '15 years', answeredAt: now() },
  suggestions: [{ id: 'S-9', text: '15 years', rationale: 'Program spec says 15+.', recommended: true }],
};

export const questions: QuestionView[] = [question1, question2];

// --- environments ---
export const environments: EnvironmentView[] = [
  {
    environmentKey: 'env-dev',
    name: 'Dev sandbox',
    baseUrl: 'https://dev.dhis2.org/api',
    environmentClass: 'TEST',
    authMode: 'PAT',
    status: 'ACTIVE',
    dhis2Version: '2.40.7',
    tlsRequired: true,
    timeoutSeconds: 60,
    llmDataPolicy: 'PRIVATE_NETWORK',
    versionConstraint: '2.40.x',
    credentials: [
      { scope: 'discovery', secretRef: 'vault://dhis2/dev/discovery', expiresAt: '2026-11-01T00:00:00Z' },
      { scope: 'import', secretRef: 'vault://dhis2/dev/import', expiresAt: '2026-11-01T00:00:00Z' },
    ],
    secretRefs: { discovery: 'vault://dhis2/dev/discovery', import: 'vault://dhis2/dev/import' },
  },
  {
    environmentKey: 'env-stg',
    name: 'Staging',
    baseUrl: 'https://staging.dhis2.org/api',
    environmentClass: 'UAT',
    authMode: 'PAT',
    versionConstraint: '2.40.x',
    status: 'DISABLED',
    tlsRequired: true,
    timeoutSeconds: 90,
    llmDataPolicy: 'PRIVATE_NETWORK',
    credentials: [
      { scope: 'discovery', secretRef: 'vault://dhis2/stg/discovery', expiresAt: '2026-10-05T00:00:00Z' },
      { scope: 'import', secretRef: 'vault://dhis2/stg/import', expiresAt: '2026-10-05T00:00:00Z' },
    ],
    secretRefs: { discovery: 'vault://dhis2/stg/discovery', import: 'vault://dhis2/stg/import' },
  },
  {
    environmentKey: 'env-prod',
    name: 'Production',
    baseUrl: 'https://prod.dhis2.org/api',
    environmentClass: 'PRODUCTION',
    authMode: 'BASIC',
    versionConstraint: '2.41.x',
    // S1's status is ACTIVE or DISABLED; whether this environment is emergency-locked is a
    // column of its protection row, answered per target by allowed-actions.
    status: 'ACTIVE',
    tlsRequired: true,
    timeoutSeconds: 120,
    llmDataPolicy: 'LOCAL_ONLY',
    // DX-AC-02: a production environment may not share one secret between reading and writing.
    credentials: [
      { scope: 'discovery', secretRef: 'vault://dhis2/prod/discovery', expiresAt: '2026-09-30T00:00:00Z' },
      { scope: 'import', secretRef: 'vault://dhis2/prod/import', expiresAt: '2026-09-30T00:00:00Z' },
    ],
    secretRefs: { discovery: 'vault://dhis2/prod/discovery', import: 'vault://dhis2/prod/import' },
  },
];

export const runs: RunView[] = [
  {
    id: 'RUN-1',
    status: 'VALIDATED',
    environmentKey: 'env-dev',
    environmentClass: 'TEST',
    projectId: DESIGN_PROJECT_ID,
    projectName: project1.name,
    workflowId: WORKFLOW_ID,
    blueprintVersion: 'v7',
    outdated: false,
    lastVerifiedAt: now(),
    validatePayloadHash: 'sha256:payload-1',
  },
];

// --- environment bindings (UI-DEP-003, S5IF-BP09/10/11) ---
/** Mutable so the mock's PUT can bind a key the way S1's does; the nullable fields are what
 *  the route actually answers (an unresolved binding has no uid and no resolution). */
export interface MockBinding {
  businessKey: string;
  uid: string | null;
  resolution: string | null;
  resolvedBy: string | null;
  note: string | null;
  pendingReparse: boolean;
  environmentKey: string;
  blueprintVersion: string;
  observedLastUpdated: string | null;
  observedHash: string | null;
  discoveryResultId: string | null;
}

export const bindings: MockBinding[] = [
  {
    businessKey: 'dataElement.weight',
    uid: 'UomZjGhT5bc',
    resolution: 'AUTO',
    resolvedBy: null,
    note: 'matched by code',
    pendingReparse: false,
    environmentKey: 'env-dev',
    blueprintVersion: 'v7',
    observedLastUpdated: null,
    observedHash: null,
    discoveryResultId: 'DISC-1',
  },
  {
    businessKey: 'categoryOptionGroupSet.ageBand',
    uid: null,
    resolution: null,
    resolvedBy: null,
    note: null,
    pendingReparse: false,
    environmentKey: 'env-dev',
    blueprintVersion: 'v7',
    observedLastUpdated: null,
    observedHash: null,
    discoveryResultId: 'DISC-1',
  },
];

/** The stored discovery result this run may bind to — searched server-side, so it is long. */
export const candidateObjects = [
  { uid: 'YrB8xKN9yOl', code: 'AGE_BAND_0_5', name: 'Under 5', contentHash: 'sha256:cb1', lastUpdated: now(), matching: true, dhis2Type: 'categoryOptionGroupSet' },
  { uid: 'fmuvtmJAEc7', code: 'AGE_BAND_5_14', name: '5 to 14', contentHash: 'sha256:cb2', lastUpdated: now(), matching: false, dhis2Type: 'categoryOptionGroupSet' },
  { uid: 'v1a1zQ8cGj1', code: 'AGE_BAND_15_49', name: '15 to 49', contentHash: 'sha256:cb3', lastUpdated: now(), matching: false, dhis2Type: 'categoryOptionGroupSet' },
  { uid: 'wlv5tHuMFA4', code: 'AGE_BAND_50_PLUS', name: '50 and above', contentHash: 'sha256:cb4', lastUpdated: now(), matching: false, dhis2Type: 'categoryOptionGroupSet' },
];

// --- notifications ---
export const notifications: NotificationPage = {
  total: 3,
  items: [
    { id: 'N-1', type: 'gate_pending', message: 'Requirements gate is awaiting approval.', projectId: DESIGN_PROJECT_ID, read: false, createdAt: now(), objectRef: { gateId: 'GT-1' } },
    { id: 'N-2', type: 'execution_failed', message: 'Discovery run failed for ANC Tally.', projectId: HANDOFF_PROJECT_ID, read: false, createdAt: now(), objectRef: {} },
    { id: 'N-3', type: 'execution_completed', message: 'Domain model run completed.', projectId: DESIGN_PROJECT_ID, read: true, createdAt: now(), objectRef: {} },
  ],
};

// --- governance (loosely typed fixtures mirroring S1 ModelView/ProviderView/PolicyView) ---
// S3's model reference is `provider/model` and S1 forwards it whole, so the mock carries the same
// shape (a slash inside a path parameter, which is why PATCH /governance/models/{ref} is declared
// with `{ref:path}`).
export const models: { items: ModelView[] } = {
  items: [
    { ref: 'openai/gpt-x-large', id: 'openai/gpt-x-large', model: 'gpt-x-large', provider: 'openai', providerClass: 'APPROVED_EXTERNAL', endpoint: 'https://api.openai.example/v1', enabled: true, capabilities: { structuredOutput: true, vision: true, maxContextTokens: 128000 }, unitPriceInputPer1k: 0.01, unitPriceOutputPer1k: 0.03, applicableAgents: { REQUIREMENTS: 2, MAPPING: 1 }, apiKeySecretRef: 'vault://models/openai/key' },
    { ref: 'self-hosted/local-llama-70b', id: 'self-hosted/local-llama-70b', model: 'local-llama-70b', provider: 'self-hosted', providerClass: 'PRIVATE_NETWORK', endpoint: 'http://llm.internal:8080', enabled: true, capabilities: { structuredOutput: false, vision: false, maxContextTokens: 8000 }, unitPriceInputPer1k: 0, unitPriceOutputPer1k: 0, applicableAgents: { UI: 1 }, apiKeySecretRef: 'vault://models/oss/key' },
  ],
};
/**
 * The eight agents of the AI-AG table, as `GET /governance/agents` answers them. A registration form
 * that cannot name the agents cannot offer a priority for one, so this list is what makes the
 * applicability block render at all — the values are S3's own (`agents/spec.py`), not a fixture's
 * invention: four of these agents share the page-design stage, which is why an agent is not a stage.
 */
export const agents: { items: AgentSpecView[] } = {
  items: [
    { agentType: 'REQUIREMENTS', stage: 'REQUIREMENTS', enabled: true, promptName: 'requirements.consolidate', patchMode: 'PATCH' },
    { agentType: 'DOMAIN', stage: 'DOMAIN', enabled: true, promptName: 'domain.model', patchMode: 'PATCH' },
    { agentType: 'MAPPING', stage: 'MAPPING', enabled: true, promptName: 'mapping.decide', patchMode: 'PATCH' },
    { agentType: 'METADATA_DESIGN', stage: 'MAPPING', enabled: true, promptName: 'metadata_design.expression', patchMode: 'PATCH' },
    { agentType: 'API', stage: 'PAGE_DESIGN', enabled: true, promptName: 'api.design', patchMode: 'PATCH' },
    { agentType: 'UI', stage: 'PAGE_DESIGN', enabled: true, promptName: 'ui.design', patchMode: 'PATCH', perPage: true },
    { agentType: 'FIXTURE', stage: 'PAGE_DESIGN', enabled: true, promptName: 'fixture.specify', patchMode: 'PATCH', perPage: true },
    { agentType: 'REVIEW', stage: 'REQUIREMENTS', enabled: true, promptName: 'review.explain', patchMode: 'EXPLAIN_ONLY' },
  ],
};
export const providers = {
  items: [
    { name: 'openai', id: 'openai', providerClass: 'APPROVED_EXTERNAL', adapter: 'openai_compat', endpoint: 'https://api.openai.example/v1', enabled: true, apiKeySecretRef: 'vault://models/openai/key', vendor: 'openai', suggestedModels: ['gpt-4o', 'gpt-4o-mini'] },
    { name: 'self-hosted', id: 'self-hosted', providerClass: 'PRIVATE_NETWORK', adapter: 'openai_compat', endpoint: 'https://llm.internal/v1', enabled: true, apiKeySecretRef: 'vault://models/oss/key', vendor: 'custom', suggestedModels: [] },
  ],
};
/**
 * S1's `PolicyView`: two matrices that only mean something together. `effective` is where the
 * organisation is (AI-POL-002's default: both external classes denied above INTERNAL),
 * `ceiling` is how far it may be widened — and the relaxation form offers the difference.
 * Answering the ceiling as the effective policy, as this fixture used to, leaves a form with
 * nothing legal to ask for.
 */
export const policy: PolicyView = {
  effective: {
    allowed: {
      PUBLIC: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      INTERNAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      CONFIDENTIAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      RESTRICTED: ['LOCAL_ONLY'],
      PHI_PII: [],
    },
    sources: [],
    warnings: [],
  },
  ceiling: {
    allowed: {
      PUBLIC: ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL', 'UNRESTRICTED'],
      INTERNAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK', 'APPROVED_EXTERNAL'],
      CONFIDENTIAL: ['LOCAL_ONLY', 'PRIVATE_NETWORK'],
      RESTRICTED: ['LOCAL_ONLY'],
      // AC-POL-02: patient data never leaves, so no relaxation reaches this row
      PHI_PII: [],
    },
    sources: ['ceiling'],
    warnings: [],
  },
  isDefault: true,
};
/** The six dimensions S1's `usage_rollup` accepts (AI-COST-001); the picker reads them, never a local list. */
export const usageDimensions = ['project', 'workflow', 'stage', 'agent', 'model', 'provider'];

export const usage = {
  groupBy: ['stage', 'agent', 'model'],
  dimensions: usageDimensions,
  items: [
    {
      projectId: DESIGN_PROJECT_ID,
      workflowId: 'WF-1',
      stage: 'REQUIREMENTS',
      agentType: 'REQUIREMENTS_REVIEW',
      model: 'gpt-test',
      provider: 'openai',
      calls: 12,
      inputTokens: 48000,
      outputTokens: 9000,
      estimatedCost: 4.2,
      failedCalls: 0,
    },
    {
      projectId: DESIGN_PROJECT_ID,
      workflowId: 'WF-1',
      stage: 'DOMAIN',
      agentType: 'DOMAIN_EXTRACT',
      model: 'gpt-test',
      provider: 'openai',
      calls: 3,
      inputTokens: 15000,
      outputTokens: 2500,
      estimatedCost: 1.1,
      failedCalls: 1,
    },
  ],
};

/**
 * AI-COST-002/003: `state` is S3's verdict and the mock keeps that authority — a screen may not
 * compare the numbers itself, or it would disagree with the rule that refuses runs.
 */
export const budgets: Record<string, { projectId: string; state: string; spent: number; limit: number | null; currency: string; thresholdReached: number | null }> = {
  [DESIGN_PROJECT_ID]: {
    projectId: DESIGN_PROJECT_ID,
    state: 'WARNING',
    spent: 4.2,
    limit: 5,
    currency: 'USD',
    thresholdReached: 0.8,
  },
};

export const emptyBudget = (projectId: string) => ({
  projectId,
  state: 'UNSET',
  spent: 0,
  limit: null,
  currency: 'USD',
  thresholdReached: null,
});

/**
 * M4 session state for the mock (UI-AUTH-001).  `off` is the default so every existing screen and
 * test behaves exactly as it did before identity existed; set `VITE_AUTH_MODE=local` to exercise
 * sign-in, the CSRF header and a refused write.
 */
export const auth = {
  mode: (import.meta.env?.VITE_AUTH_MODE as string | undefined) ?? 'off',
  accounts: { 'ada@example.org': 'correct-horse' } as Record<string, string>,
  session: {
    authenticated: false,
    userId: null as string | null,
    subject: null as string | null,
    displayName: null as string | null,
    roles: [] as string[],
    csrfToken: null as string | null,
    expiresAt: null as string | null,
    mustChangePassword: false,
  },
  /** Sign in only the account the mock knows; anything else is refused the way S1 refuses it. */
  signIn(subject: string, password: string): boolean {
    if (this.accounts[subject] !== password) return false;
    this.session = {
      authenticated: true,
      userId: `usr-${subject.split('@')[0]}`,
      subject,
      displayName: subject.split('@')[0] ?? subject,
      // a role S1 actually issues (`identity/roles.py:BUILT_IN_ROLES`); an invented name here would
      // teach the console a vocabulary the server refuses at the boundary
      roles: ['SOLUTION_ARCHITECT'],
      csrfToken: 'mock-csrf-' + Math.random().toString(36).slice(2, 10),
      expiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
      mustChangePassword: false,
    };
    return true;
  },
  signOut(): void {
    this.session = {
      authenticated: false,
      userId: null,
      subject: null,
      displayName: null,
      roles: [],
      csrfToken: null,
      expiresAt: null,
      mustChangePassword: false,
    };
  },
};


/** M4 administration fixtures (CP-IAM-002, UI-AUTH-003). Values are shaped like S1's views.
 *
 * Loosely typed on purpose: the mock is a stand-in for S1's answers, and a row written back by a
 * handler carries the fields the request had. The screens read them through the generated view types. */
type AdminRow = Record<string, unknown>;
export const users: AdminRow[] = [
  {
    id: 'USR-ada', subject: 'ada@example.org', source: 'local', displayName: 'ada', email: 'ada@example.org', status: 'ACTIVE', mustChangePassword: false, locked: false, lastLoginAt: null, createdAt: '2026-09-01T08:00:00Z',
    // what the account actually holds, as S1's UserView reports it: revoking names one of these
    roles: [{ userId: 'USR-ada', role: 'SOLUTION_ARCHITECT', projectId: DESIGN_PROJECT_ID, revoked: false }],
  },
  {
    id: 'USR-cyrus', subject: 'cyrus@example.org', source: 'local', displayName: 'Cyrus', email: 'cyrus@example.org', status: 'DISABLED', mustChangePassword: true, locked: false, lastLoginAt: null, createdAt: '2026-09-02T08:00:00Z',
    roles: [],
  },
];

export const roles: AdminRow[] = [
  { name: 'SOLUTION_ARCHITECT', actions: ['project.edit', 'patch.submit', 'stage.run'] },
  { name: 'DEPLOYMENT_APPROVER', actions: ['gate.approve', 'execution.commit'] },
  { name: 'PLATFORM_ADMINISTRATOR', actions: ['user.manage', 'member.manage', 'restricted_grant.manage', 'production_exemption.manage'] },
];

export const members: AdminRow[] = [
  { userId: 'USR-ada', projectId: DESIGN_PROJECT_ID, role: 'SOLUTION_ARCHITECT', subject: 'ada@example.org' },
];

export const grants: AdminRow[] = [
  { id: 'GRA-1', projectId: DESIGN_PROJECT_ID, userId: 'USR-ada', subject: 'ada@example.org', classification: 'PHI_PII', reason: 'ward list review', grantedBy: 'ada@example.org', expiresAt: null, revoked: false },
];

export const exemptions: AdminRow[] = [
  { id: 'EXM-1', projectId: DESIGN_PROJECT_ID, waiver: 'COMMIT_WINDOW', environmentKey: 'env-prod', reason: 'regional reporting deadline', grantedBy: 'ada@example.org', expiresAt: '2026-10-05T00:00:00Z', revoked: false },
];

/** AI-CHAT-003: one conversation per project stage, clearable. */
export const chatTranscripts: Record<string, { role: string; content: string }[]> = {
  'ST-req': [
    { role: 'user', content: 'Which ward list did this come from?' },
    { role: 'assistant', content: 'From the second sheet of the uploaded list; the first was a header.' },
  ],
};


// --- system settings (mirrors S1 SystemSettingsView fields) ---
/**
 * `GET /settings` answers `SystemSettingList`: the seven keys as a list *and* as one typed
 * `values` object (S5IF-FLD10). The mock used to hand back the bare object, which is how the
 * console's wrong read survived its own tests — it agreed with a shape S1 never sends.
 */
const settingsValues = {
  idempotencyRetentionHours: 24,
  eventRetentionDays: 7,
  defaultGateReminderHours: 1,
  maxUploadBytes: 26214400,
  allowedUploadTypes: ['pdf', 'docx', 'xlsx', 'csv', 'md', 'txt'],
  platformNotificationChannels: [
    { type: 'email', target: 'ops@example.org', secretRef: null },
    { type: 'webhook', target: 'https://hooks.example.org/damf', secretRef: 'db://hooks/ops' },
  ],
  archivedProjectRetentionDays: 365,
};

export const systemSettings = {
  items: Object.entries(settingsValues).map(([key, value]) => ({ key, value })),
  values: settingsValues,
  total: Object.keys(settingsValues).length,
  limit: 100,
  offset: 0,
};

// --- blueprint draft + section schema (drives manual authoring) ---
export const draft = {
  draftId: 'DRF-1',
  stage: 'REQUIREMENTS',
  stageScope: ['REQUIREMENTS'],
  contentHash: 'sha256:ab12',
  openPatchCount: 2,
  promotable: true,
  status: 'OPEN',
  document: {
    requirements: [
      { businessKey: 'req:register-visit', text: 'A CHW registers a household visit.', priority: 'MUST' },
      { businessKey: 'req:eligibility', text: 'People aged 15+ are eligible for the program.', priority: 'SHOULD' },
    ],
  },
};

export const sectionSchemaIndex = {
  currentSchemaVersion: 1,
  availableVersions: [1],
  sections: [{ section: 'requirements', keyField: 'businessKey', entryContainer: 'requirements' }],
};

export const requirementsSectionSchema = {
  $id: 'https://damf/s2/requirements',
  title: 'Requirements',
  type: 'object',
  properties: {
    requirements: {
      type: 'array',
      items: {
        type: 'object',
        required: ['businessKey', 'text'],
        properties: {
          businessKey: { type: 'string' },
          text: { type: 'string' },
          priority: { type: 'string', enum: ['MUST', 'SHOULD', 'COULD', 'WONT'] },
        },
      },
    },
  },
};

export const registry: RegistryPage = {
  total: 2,
  items: [
    // a row that had to choose carries the decision it chose with (S5IF-BP13), which is the only way
    // the 决策 column — and the page behind it — is ever exercised below the fixture level
    { businessKey: 'entity:facility', dhis2Type: 'organisationUnit', disposition: 'CREATE', managed: true, uid: 'a1b2c3d4e5f60718', code: 'FACILITY', decisionId: 'MAP-017' },
    { businessKey: 'rule:age-range', dhis2Type: 'programRule', disposition: 'UPDATE', managed: true, code: 'AGE_RULE', decisionId: 'ARC-002' },
  ],
};

export const decisions: DecisionList = {
  items: [
    {
      decisionId: 'MAP-017',
      type: 'MAPPING',
      subject: 'entity:facility',
      selected: { dhis2Type: 'organisationUnit', structure: 'facility registry' },
      alternatives: [{ dhis2Type: 'dataElement', note: 'a flat list loses the hierarchy' }],
      rationale: 'The sheet describes units that contain people, so it maps to the org-unit tree rather than to a data element.',
      risks: [{ level: 'MEDIUM', note: 'renamed units break the parent lookup' }],
      confidence: 0.78,
      impactLevel: 'MEDIUM',
      confirmation: { operator: 'Ana Ruiz' },
      affectedArtifacts: ['ART-PKG-1'],
    },
    {
      decisionId: 'ARC-002',
      type: 'ARCHITECTURE',
      selected: { decision: 'one program per care modality' },
      alternatives: [],
      rationale: 'Each modality has its own approval line, so one program per modality keeps the gates apart.',
      risks: [],
      confidence: null,
      impactLevel: 'HIGH',
      confirmation: { questionId: 'Q-7', answerer: 'Bui Ho' },
      affectedArtifacts: [],
    },
  ],
};

export const inputs: InputPage = {
  total: 2,
  items: [
    { artifactId: 'INP-1', filename: 'spec.xlsx', mediaType: 'application/xlsx', sizeBytes: 148213, dataClassification: 'INTERNAL', parseStatus: 'PARSED', parserType: 'XLSX', parserVersion: '1.4.0', usedModel: false, parseId: 'PARSE-1', nrrArtifactId: 'NRR-1', createdAt: now() },
    // UI-REQS-002: a mapping S3 could not resolve without the user, so the row offers the review.
    { artifactId: 'INP-2', filename: 'indicators.csv', mediaType: 'text/csv', sizeBytes: 9012, dataClassification: 'CONFIDENTIAL', parseStatus: 'NEEDS_REVIEW', parserType: 'CSV', parserVersion: '1.4.0', usedModel: false, parseId: 'PARSE-2', createdAt: now() },
  ],
};

export const llmLog = {
  parseStatus: 'PARSED',
  parseRunId: 'ipr-1',
  total: 2,
  limit: 50,
  offset: 0,
  items: [
    {
      exchangeId: 'aexg-1',
      sequence: 1,
      status: 'OK',
      provider: 'mock',
      providerClass: 'LOCAL_ONLY',
      model: 'mock-agent',
      requestId: 'req-1',
      inputTokens: 120,
      outputTokens: 40,
      latencyMs: 1200,
      createdAt: now(),
      completedAt: now(),
    },
    {
      exchangeId: 'aexg-2',
      sequence: 2,
      status: 'ERROR',
      provider: 'mock',
      providerClass: 'LOCAL_ONLY',
      model: 'mock-agent',
      inputTokens: 0,
      outputTokens: 0,
      latencyMs: 300000,
      errorCode: 'LLM_TIMEOUT',
      errorMessage: 'model request timed out after 300.0s',
      createdAt: now(),
    },
  ],
};

export const llmExchange = {
  ...llmLog.items[0],
  inputArtifactId: 'INP-1',
  request: {
    messages: [
      { role: 'system', content: 'system rules', images: [] },
      { role: 'user', content: '<untrusted kind="INPUT_ARTIFACT">a,b</untrusted>', images: [] },
    ],
    schemaName: 'extraction',
    temperature: 0,
    maxOutputTokens: 8000,
  },
  response: {
    text: '{"items": []}',
    payload: { items: [] },
    finishReason: 'stop',
    model: 'mock-agent',
    requestId: 'req-1',
    usage: { inputTokens: 120, outputTokens: 40 },
    storageTruncated: false,
  },
  expiresAt: now(),
};

/**
 * S5IF-BP17: the files one build produced (UI-REV-003's by-file view). `README.md` carries no
 * object, so its count is null rather than zero — the screen shows "—" and not "empty package".
 */
export const packageFiles = {
  projectId: DESIGN_PROJECT_ID,
  version: 'v7',
  buildId: 'bld-7',
  kind: 'ENVIRONMENT',
  environmentKey: 'env-dev',
  packageHash: 'sha256:pkg3',
  files: [
    { path: 'metadata.json', role: 'payload', contentHash: 'sha256:pay1', dhis2Type: null, objectCount: 2 },
    { path: 'metadata_manifest.json', role: 'manifest', contentHash: 'sha256:man1', dhis2Type: null, objectCount: null },
    { path: 'dependency-graph.json', role: 'graph', contentHash: 'sha256:grh1', dhis2Type: null, objectCount: null },
    { path: 'views/dataElements.json', role: 'view', contentHash: 'sha256:v01', dhis2Type: 'dataElement', objectCount: 1 },
    { path: 'views/organisationUnits.json', role: 'view', contentHash: 'sha256:v02', dhis2Type: 'organisationUnit', objectCount: 1 },
    { path: 'README.md', role: 'document', contentHash: 'sha256:rdm1', dhis2Type: null, objectCount: null },
  ],
  total: 6,
};

export const uploadLimits = { maxUploadBytes: 26214400, allowedUploadTypes: ['text/csv', 'application/xlsx', 'text/markdown'] };

/**
 * CP-UI-021: the mode-B development package — S2's own layout, forwarded whole. `kind` is S2's word
 * for a file's role here and deliberately not the build package's `role`, because the two packages
 * answer different questions. It belongs to the mode-B project: a mode-A project skips the HANDOFF
 * stage (CP-PRJ-005), so the honest answer there is "not generated".
 */
export const frontendPackage = {
  projectId: HANDOFF_PROJECT_ID,
  artifactId: 'ART-HOF-1',
  blueprintVersion: 'v7',
  files: [
    { path: 'README.md', kind: 'DOCUMENT', sizeBytes: 1840, contentHash: 'sha256:hof0' },
    { path: 'src/api/dataElements.ts', kind: 'CONTRACT', sizeBytes: 2410, contentHash: 'sha256:hof1' },
    { path: 'src/ui/patientForm.json', kind: 'UI_SPEC', sizeBytes: 980, contentHash: 'sha256:hof2' },
  ],
};

/** Two of the three files, read as text. The third is deliberately absent: it is the NOT_EXIST arm. */
export const frontendPackageFiles: Record<string, string> = {
  'src/api/dataElements.ts': 'export interface DataElementDto { id: string; name: string; }\n',
  'src/ui/patientForm.json': '{"page":"patientForm","fields":["name","birthDate"]}\n',
};
/**
 * S5IF-BP16: the same version's graph. One object is deliberately unconnected and one reference
 * crosses types; the degrees and the orphan flag are S1's arithmetic over S2's stored edges.
 */
export const objectGraph = {
  workflowId: WORKFLOW_ID,
  projectId: DESIGN_PROJECT_ID,
  kind: 'BASELINE',
  blueprintVersion: '0.1.0',
  buildId: 'bld-0001',
  runId: null,
  schemaVersion: 1,
  environmentKey: null,
  // nodes are Registry entries only (BP-TD-006): status, code and the environment bindings belong
  // to the Registry read, so the graph fixture must not carry them either
  nodes: [
    { businessKey: 'entity:facility', dhis2Type: 'organisationUnit', uid: 'a1b2c3d4e5f60718', disposition: 'CREATE', managed: true, inPayload: true, inDegree: 1, outDegree: 1 },
    { businessKey: 'rule:age-range', dhis2Type: 'programRule', uid: null, disposition: 'UPDATE', managed: true, inPayload: true, inDegree: 1, outDegree: 1 },
    { businessKey: 'form:anc', dhis2Type: 'programRule', uid: null, disposition: 'CREATE', managed: true, inPayload: false, inDegree: 0, outDegree: 0 },
  ],
  edges: [
    { fromBusinessKey: 'rule:age-range', toBusinessKey: 'entity:facility', field: 'targetDataElement', fromDhis2Type: 'programRule', toDhis2Type: 'organisationUnit', edgeKind: 'ref' },
    { fromBusinessKey: 'entity:facility', toBusinessKey: 'rule:age-range', field: 'programRuleVariable', fromDhis2Type: 'organisationUnit', toDhis2Type: 'programRule', edgeKind: 'ref' },
  ],
  // orphans are a property of the whole build, not of whatever filter is on screen (BP-TD-009)
  orphans: ['form:anc'],
  filters: { dhis2Types: [], businessKeys: [] },
  unresolvedRegistryRefs: [
    { fromBusinessKey: 'form:anc', field: 'programRuleVariable', target: 'rule:triage' },
  ],
};

/** S5IF-INP03: one proposal per sheet S3 could not map on its own. */
export const columnMapping = {
  artifactId: 'INP-2',
  workflowId: WORKFLOW_ID,
  proposals: [
    {
      questionId: 'Q-3',
      sheet: 'indicators',
      question: 'Which source column holds the indicator name?',
      headers: ['col1', 'col2', 'col3'],
      affectedKeys: ['entity:facility'],
      columnMapping: { col1: 'name', col2: 'shortName' },
      suggestions: [{ columnMapping: { col3: 'aggregationType' } }],
    },
  ],
};

/** S5IF-BP15 / UI-REQS-004①: S3's normalised reading of the inputs. */
export const nrr = {
  total: 3,
  items: [
    { businessKey: 'req:monthly-summary', category: 'reporting', statement: 'A monthly summary per facility is required.', confidence: 0.92, source: { artifactId: 'INP-1' }, status: 'CONFIRMED', priority: 'MUST', openQuestions: [], actors: ['data manager'] },
    { businessKey: 'req:age-range', category: 'validation', statement: 'Age must be between 0 and 120.', confidence: 0.71, source: { artifactId: 'INP-1' }, status: 'CONFIRMED', priority: 'SHOULD', openQuestions: [], actors: [] },
    { businessKey: 'req:export-sms', category: 'reporting', statement: 'Export weekly totals by SMS.', confidence: 0.42, source: { artifactId: 'INP-2' }, status: 'NEEDS_REVIEW', priority: 'COULD', openQuestions: ['Is the SMS export weekly or monthly?'], actors: [] },
  ],
};


/** UI-OPS-002: enough audit rows that a filter visibly narrows them. */
export const auditRecords = [
  { id: 'AUD-1', eventType: 'cp.project.created', operatorName: 'Ana', operatorType: 'HUMAN', objectId: 'PRJ-green-01', objectType: 'Project', sourceSubsystem: 'S1', occurredAt: '2026-03-01T09:00:00Z', hash: 'h1' },
  { id: 'AUD-2', eventType: 'cp.stage.passed', operatorName: 'Ben', operatorType: 'HUMAN', objectId: 'ST-req', objectType: 'Stage', sourceSubsystem: 'S1', occurredAt: '2026-03-02T09:00:00Z', hash: 'h2' },
  { id: 'AUD-3', eventType: 'cp.gate.approved', operatorName: 'Ana', operatorType: 'HUMAN', objectId: 'GT-1', objectType: 'Gate', sourceSubsystem: 'S1', occurredAt: '2026-03-03T09:00:00Z', hash: 'h3' },
];

export const commitTrace = {
  executionId: 'EX-9',
  environmentKey: 'env-dev',
  blueprintVersion: 'v7',
  gateStatus: 'APPROVED',
  commitOperator: 'Ana',
  packageArtifactId: 'ART-PKG-1',
  importReportArtifactId: 'ART-IMP-1',
  approvals: [{ role: 'DEVELOPER', operatorName: 'Ana', decidedAt: '2026-03-03T10:00:00Z' }],
  auditTrail: auditRecords,
};

/** UI-OPS-001: two requests in two states, so per-target availability is visible. */
export const changeRequests = [
  { id: 'CR-1', projectId: DESIGN_PROJECT_ID, status: 'ANALYZED', reason: 'Add birth-weight aggregation', affectedBusinessKeys: ['entity:facility'], requestedBy: 'Ana', impactReportArtifactId: 'ART-IMP-1' },
  { id: 'CR-2', projectId: DESIGN_PROJECT_ID, status: 'APPROVED', reason: 'Rename the tally page', affectedBusinessKeys: ['page:daily-tally'], requestedBy: 'Ben' },
];

/* ---- UI-REV-001 / 003 / 005 fixtures ---- */

export const openApiYaml = `openapi: 3.1.0
info:
  title: Facility Register API
  version: 1.0.0
  description: Generated from the approved domain model.
servers:
  - url: https://ims.example.org/api
tags:
  - name: patients
    description: Patient register pages
paths:
  /patients:
    get:
      operationId: listPatients
      summary: List patients
      tags: [patients]
      parameters:
        - name: pageSize
          in: query
          required: false
          schema:
            type: integer
      responses:
        '200':
          description: A page of patients
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PatientPage'
  /patients/{id}:
    get:
      operationId: getPatient
      summary: Read one patient
      tags: [patients]
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: The patient
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Patient'
components:
  schemas:
    Patient:
      type: object
      properties:
        id:
          type: string
        displayName:
          type:
            - string
            - 'null'
    PatientPage:
      type: object
      properties:
        items:
          type: array
          items:
            $ref: '#/components/schemas/Patient'
`;

export const artifacts = [
  // UI-REV-004: the compiled interface model. S2 writes it as YAML (`api/openapi.yaml`), so the
  // fixture is YAML too — a JSON-only fixture would let a JSON.parse-only viewer look complete.
  { id: 'ART-API-1', type: 'OPENAPI', version: 1, status: 'APPROVED', contentHash: 'sha256:api1', size: 1400, mediaType: 'application/vnd.oai.openapi', dataClassification: 'INTERNAL', creatorSubsystem: 'S2', creatorOperator: 'Ana', createdAt: '2026-03-03T08:30:00Z', contentPurged: false, sourceArtifactIds: ['ART-DS-1'], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-PKG-1', type: 'ENV_PACKAGE', version: 3, status: 'APPROVED', contentHash: 'sha256:pkg3', size: 20480, mediaType: 'application/zip', dataClassification: 'INTERNAL', creatorSubsystem: 'S2', creatorOperator: 'Ana', createdAt: '2026-03-03T09:00:00Z', contentPurged: false, prevVersionId: 'ART-PKG-2', sourceArtifactIds: ['ART-VAL-1'], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-PKG-2', type: 'ENV_PACKAGE', version: 2, status: 'SUPERSEDED', contentHash: 'sha256:pkg2', size: 20100, mediaType: 'application/zip', dataClassification: 'INTERNAL', creatorSubsystem: 'S2', creatorOperator: 'Ana', createdAt: '2026-03-02T09:00:00Z', contentPurged: false, sourceArtifactIds: [], projectId: DESIGN_PROJECT_ID },
  // CP-UI-021: the artifact the Handoff section reads its file listing through — S2 produced it at
  // the HANDOFF stage, and the package's own files hang off this record.
  { id: 'ART-HOF-1', type: 'FRONTEND_PACKAGE', version: 1, status: 'APPROVED', contentHash: 'sha256:hof9', size: 5230, mediaType: 'application/zip', dataClassification: 'INTERNAL', creatorSubsystem: 'S2', creatorOperator: 'Ana', createdAt: '2026-03-03T09:30:00Z', contentPurged: false, sourceArtifactIds: ['ART-API-1'], projectId: HANDOFF_PROJECT_ID },
  { id: 'ART-VAL-1', type: 'VALIDATION_REPORT', version: 1, status: 'APPROVED', contentHash: 'sha256:val1', size: 900, mediaType: 'application/json', dataClassification: 'INTERNAL', creatorSubsystem: 'S2', creatorOperator: 'Ana', createdAt: '2026-03-03T08:00:00Z', contentPurged: false, sourceArtifactIds: ['INP-1'], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-PHI-1', type: 'BUSINESS_INPUT', version: 1, status: 'APPROVED', contentHash: 'sha256:phi1', size: 4096, mediaType: 'text/csv', dataClassification: 'PHI_PII', creatorSubsystem: 'S1', creatorOperator: 'Ana', createdAt: '2026-03-01T08:00:00Z', contentPurged: true, sourceArtifactIds: [], projectId: DESIGN_PROJECT_ID },
  // UI-DEP-005: the documents one COMMIT produces, read by id from the run console
  { id: 'ART-EXR-1', type: 'EXECUTION_REPORT', version: 1, status: 'APPROVED', contentHash: 'sha256:exr1', size: 1200, mediaType: 'application/json', dataClassification: 'INTERNAL', creatorSubsystem: 'S4', creatorOperator: 'Ana', createdAt: '2026-03-04T09:00:00Z', contentPurged: false, sourceArtifactIds: ['ART-PKG-1'], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-IMP-1', type: 'IMPORT_REPORT', version: 1, status: 'APPROVED', contentHash: 'sha256:imp1', size: 1500, mediaType: 'application/json', dataClassification: 'INTERNAL', creatorSubsystem: 'S4', creatorOperator: 'Ana', createdAt: '2026-03-04T09:01:00Z', contentPurged: false, sourceArtifactIds: ['ART-EXR-1'], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-PV-1', type: 'POST_VERIFY_REPORT', version: 1, status: 'APPROVED', contentHash: 'sha256:pv1', size: 800, mediaType: 'application/json', dataClassification: 'INTERNAL', creatorSubsystem: 'S4', creatorOperator: 'Ana', createdAt: '2026-03-04T09:20:00Z', contentPurged: false, sourceArtifactIds: ['ART-SNP-1'], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-SNP-1', type: 'SNAPSHOT', version: 1, status: 'APPROVED', contentHash: 'sha256:snp1', size: 24000, mediaType: 'application/json', dataClassification: 'INTERNAL', creatorSubsystem: 'S4', creatorOperator: 'Ana', createdAt: '2026-03-04T08:55:00Z', contentPurged: false, sourceArtifactIds: [], projectId: DESIGN_PROJECT_ID },
  { id: 'ART-DIFF-1', type: 'DIFF_REPORT', version: 1, status: 'APPROVED', contentHash: 'sha256:dif1', size: 700, mediaType: 'application/json', dataClassification: 'INTERNAL', creatorSubsystem: 'S4', creatorOperator: 'Ana', createdAt: '2026-03-04T09:21:00Z', contentPurged: false, sourceArtifactIds: ['ART-SNP-1'], projectId: DESIGN_PROJECT_ID },
];

export const artifactContent = '{"requirements":[{"businessKey":"req:monthly-summary","text":"A monthly summary per facility is required.","priority":"MUST"}]}';

/**
 * UI-DEP-005: the four documents a COMMIT leaves behind. Each is read by artifact id, and the
 * mock answers per id so a screen that shows "the ImportReport as DHIS2 returned it" cannot be
 * passing on the validation report's bytes.
 */
export const artifactContents: Record<string, string> = {
  'ART-API-1': openApiYaml,
  'ART-EXR-1': JSON.stringify({
    executionId: 'EX-9',
    applied: true,
    steps: [
      { name: 'prepare', status: 'DONE' },
      { name: 'validate', status: 'DONE' },
      { name: 'commit', status: 'DONE' },
    ],
    issues: [],
  }),
  'ART-IMP-1': JSON.stringify({
    status: 'WARNING',
    responseType: 'OK',
    importSummaries: [{ type: 'dataElement', state: 'WARNING', imported: 4, updated: 1 }],
    feedbackMessages: [{ object: 'o2mEEk3iP41', message: 'name already exists, merged' }],
  }),
  'ART-PV-1': JSON.stringify({
    consistent: false,
    differences: [{ businessKey: 'dataElement.weight', expected: '2.41', found: '2.40' }],
  }),
  'ART-DIFF-1': JSON.stringify({
    against: 'snapshot ART-SNP-1',
    changed: [{ uid: 'o2mEEk3iP41', field: 'name', from: 'Weight', to: 'Weight (kg)' }],
  }),
};

export const lineage = { artifactId: 'ART-PKG-1', sources: ['ART-VAL-1'], derived: [] };

export const artifactDiff = { aId: 'ART-PKG-1', bId: 'ART-PKG-2', changed: true, kind: 'OBJECTS', lines: [{ op: 'replace', path: '/metadata/dataElements/0/name', from: 'Weight', to: 'Weight (kg)' }] };

export const issues = [
  { issueId: 'ISS-1', level: 'BLOCKING', description: 'shortName is missing', ruleId: 'BPV-ST-004', businessKey: 'entity:facility', dhis2Type: 'trackedEntityAttribute', sourceStage: 'DOMAIN', artifactId: 'ART-VAL-1', location: 'domain.objects[0]' },
  { issueId: 'ISS-2', level: 'WARNING', description: 'aggregationType not supported by this profile', ruleId: 'BPV-XA-002', businessKey: 'rule:age-range', dhis2Type: 'programRule', sourceStage: 'MAPPING', artifactId: 'ART-VAL-1' },
];

export const blueprintVersions = [
  { version: 'v6', projectId: DESIGN_PROJECT_ID, changeLevel: 'MINOR', contentHash: 'sha256:v6', createdAt: '2026-03-01T10:00:00Z', createdBy: 'Ana', status: 'SUPERSEDED', parentVersion: 'v5', decisionIds: [] },
  { version: 'v7', projectId: DESIGN_PROJECT_ID, changeLevel: 'MAJOR', contentHash: 'sha256:v7', createdAt: '2026-03-03T10:00:00Z', createdBy: 'Ana', status: 'CURRENT', parentVersion: 'v6', decisionIds: ['DEC-1'] },
];

export const blueprintDiff = {
  projectId: DESIGN_PROJECT_ID,
  fromVersion: 'v6',
  toVersion: 'v7',
  kind: 'VERSIONS',
  counts: { create: 1, update: 1, delete: 0 },
  objects: [
    { businessKey: 'entity:facility', dhis2Type: 'trackedEntityType', state: 'ADDED', disposition: 'CREATE', section: 'domain' },
    { businessKey: 'rule:age-range', dhis2Type: 'programRule', state: 'UPDATED', disposition: 'UPDATE', section: 'rules', changes: [{ field: 'expression', from: 'age > 5', to: 'age >= 5' }] },
  ],
};

/** Mutable clone so handler responses can reflect POST effects within a session. */
export type MockProtection = {
  timezone: string;
  commitWindows: { days: number[]; start: string; end: string }[];
  emergencyLocked: boolean;
  lockReason: string | null;
  lockChangedAt: string | null;
  lockChangedBy: string | null;
  deletionPolicy: string | null;
};

/**
 * S4 keeps protection in its own row, and so does this mock: a lock that wrote itself onto
 * `EnvironmentView.status` would test a response S1 never sends, and the page would pass while
 * the real deployment showed the opposite.
 */
export const protections: Record<string, MockProtection> = {
  'env-dev': {
    timezone: 'UTC',
    commitWindows: [],
    emergencyLocked: false,
    lockReason: null,
    lockChangedAt: null,
    lockChangedBy: null,
    deletionPolicy: 'SAFE_ONLY',
  },
  'env-stg': {
    timezone: 'Africa/Addis_Ababa',
    commitWindows: [{ days: [2, 4], start: '01:00', end: '05:00' }],
    emergencyLocked: false,
    lockReason: null,
    lockChangedAt: null,
    lockChangedBy: null,
    deletionPolicy: 'SAFE_ONLY',
  },
  'env-prod': {
    timezone: 'Africa/Addis_Ababa',
    commitWindows: [{ days: [6], start: '22:00', end: '23:30' }],
    emergencyLocked: true,
    lockReason: '核对生产元数据期间冻结提交',
    lockChangedAt: now(),
    lockChangedBy: 'Ana',
    deletionPolicy: 'FORBID',
  },
};

/**
 * Credential *names* entered during a mock session. The values are accepted by the handler and
 * dropped on the floor: a mock that stored them would let a test pass while the real S1 answered
 * differently, and the rule under test (S5IF-G09) is that nothing reads a value back.
 */
interface SecretRow {
  name: string;
  ref: string;
  algo: string;
  createdAt: string;
  updatedAt: string;
  rotatedAt: string | null;
  createdBy: string;
}
const secrets: SecretRow[] = [];

/** Create-or-rotate, the way `PUT /secrets/{name}` does. */
export function putSecret(name: string, operator: string): SecretRow {
  const existing = secrets.find((row) => row.name === name);
  if (existing) {
    existing.rotatedAt = now();
    existing.updatedAt = now();
    existing.createdBy = operator;
    return { ...existing };
  }
  const row: SecretRow = {
    name,
    ref: `db://${name}`,
    algo: 'aes-256-gcm+hkdf-sha256',
    createdAt: now(),
    updatedAt: now(),
    rotatedAt: null,
    createdBy: operator,
  };
  secrets.push(row);
  return { ...row };
}

export function listSecrets() {
  return secrets.map((row) => ({ ...row }));
}

export function dropSecret(name: string): boolean {
  const index = secrets.findIndex((row) => row.name === name);
  if (index < 0) return false;
  secrets.splice(index, 1);
  return true;
}

/**
 * A stand-in for S1's catalogue (S5IF-GOV07). Same fields, same wire protocol, and the same
 * promise: these are addresses to start from, not endpoints that were dialled.
 */
export const providerCatalogue = [
  {
    key: 'openai',
    displayName: 'OpenAI',
    endpoint: 'https://api.openai.com/v1',
    wireProtocol: 'openai_compat',
    models: ['gpt-4o', 'gpt-4o-mini'],
    keyRefSuggestion: 'env://OPENAI_API_KEY',
    docsUrl: 'https://platform.openai.com/docs/api-reference',
    local: false,
    note: '',
  },
  {
    key: 'deepseek',
    displayName: 'DeepSeek',
    endpoint: 'https://api.deepseek.com/v1',
    wireProtocol: 'openai_compat',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    keyRefSuggestion: 'env://DEEPSEEK_API_KEY',
    docsUrl: 'https://api-docs.deepseek.com/',
    local: false,
    note: '',
  },
  {
    key: 'ollama',
    displayName: 'Ollama',
    endpoint: 'http://localhost:11434/v1',
    wireProtocol: 'openai_compat',
    models: ['qwen2.5', 'llama3.1'],
    // a local server usually authenticates with nothing; naming a variable here would be a
    // suggestion that cannot resolve
    keyRefSuggestion: '',
    docsUrl: 'https://github.com/ollama/ollama/blob/main/docs/openai.md',
    local: true,
    note: 'mock: whatever this machine loaded',
  },
];

export const state = {
  projects,
  notifications,
  environments,
  protections,
  runs,
  bindings,
  candidateObjects,
  gate1,
  questions,
  systemSettings,
  draft,
  // When each environment last passed a connection test. S4 keeps this per environment and S1
  // reports the environments that have none as "never tested" (S5IF-SET01); `env-dev` is left
  // without an entry on purpose so the guided page demonstrates the branch it produces.
  connections: { 'env-prod': '2026-09-30T08:00:00Z', 'env-stg': '2026-09-29T08:00:00Z' } as Record<string, string>,
  lastTouched: now,
};

/**
 * `GET /setup` rebuilt from the same mutable rows the environment and governance pages read, so
 * the guided checklist cannot disagree with the page it links to — and a connection test recorded
 * by a test-connection call disappears from `neverTested` on the next fetch, exactly as it does on
 * a real S1 (S5IF-SET01).
 */
export function setupView(): SetupView {
  const rows = state.environments ?? [];
  const items = rows.map((row) => {
    const inService = row.status !== 'DISABLED';
    return {
      environmentKey: row.environmentKey,
      environmentClass: row.environmentClass ?? null,
      status: inService ? 'ACTIVE' : 'DISABLED',
      lastTestedAt: inService ? (state.connections[row.environmentKey] ?? null) : null,
    };
  });
  const inServiceItems = items.filter((item) => item.status === 'ACTIVE');
  return {
    generatedAt: now(),
    engine: { name: 'inprocess', durable: false, detail: { note: 'mock' } },
    providers: { S2: 'real', S3: 'real', S4: 'real' },
    startup: null,
    configuration: {
      providerMode: 'auto',
      workflowEngine: 'inprocess',
      migrationsMode: 'apply',
      httpsOnly: true,
      authMode: 'off',
      problems: [],
    },
    identity: {
      authMode: 'off',
      enforced: false,
      m4Enabled: false,
      bootstrapAdminDeclared: false,
      users: 0,
      pendingPasswordChange: false,
    },
    environments: {
      total: items.length,
      inService: inServiceItems.length,
      nonProductionInService: inServiceItems.filter((item) => item.environmentClass !== 'PRODUCTION').length,
      neverTested: inServiceItems.filter((item) => !item.lastTestedAt).map((item) => item.environmentKey),
      items,
    },
    intelligence: {
      providers: providers.items.length,
      providersEnabled: providers.items.filter((row) => row.enabled !== false).length,
      models: models.items.length,
      modelsEnabled: models.items.filter((row) => row.enabled !== false).length,
    },
    credentials: {
      storeAvailable: true,
      masterKeyConfigured: true,
      scheme: 'db',
      named: secrets.length,
      unresolvable: [],
    },
    projects: {
      total: state.projects.length,
      active: state.projects.filter((row) => !row.archived).length,
    },
  };
}

/**
 * The platform answer rebuilt against the mutable protection state, so locking an environment in
 * a test moves the same `details.locked` the page reads — the static list would freeze it.
 */
export function platformActionsView(): AllowedActionsView {
  const disabled = new Set(
    (state.environments ?? []).filter((e) => e.status === 'DISABLED').map((e) => e.environmentKey),
  );
  // the same split S1 makes: an environment out of service answers nothing about the instance,
  // but its profile stays editable and its lock stays liftable, or nobody could switch it back
  const refusedWhenDisabled = new Set(['environment.disable', 'environment.test_connection', 'environment.probe_capabilities']);
  const decide = (entry: NonNullable<AllowedActionsView['allowed']>[number]) => {
    if (entry.action === 'environment.emergency_lock' && entry.targetId) {
      return { ...entry, details: { locked: protections[String(entry.targetId)]?.emergencyLocked === true } };
    }
    if (entry.targetId && disabled.has(String(entry.targetId)) && refusedWhenDisabled.has(entry.action)) {
      return { ...entry, available: false, reasonCode: 'ENVIRONMENT_DISABLED' };
    }
    return entry;
  };
  return {
    ...platformActions,
    allowed: (platformActions.allowed ?? []).map(decide),
  };
}

/**
 * The workflow answer rebuilt against the mutable workflow row (domain/actions.py): pause and
 * resume are one switch, so exactly one of them is available and which one follows `paused` —
 * and both go away once the workflow is cancelled.
 */
/**
 * CP-UI-021: the development package belongs to the mode-B project's workflow only. A mode-A
 * project skips the HANDOFF stage that produces one, so S1 answers the action unavailable with
 * `PACKAGE_NOT_GENERATED` — which is what the console now reads instead of guessing from a 404.
 */
export function workflowActionsView(workflowId = WORKFLOW_ID): AllowedActionsView {
  const blocked = (entry: NonNullable<AllowedActionsView['allowed']>[number], reasonCode: string) => ({
    ...entry,
    available: false,
    reasonCode,
    details: {},
  });
  const decide = (entry: NonNullable<AllowedActionsView['allowed']>[number]) => {
    if (workflow1.cancelled === true) return blocked(entry, 'WORKFLOW_CANCELLED');
    if (entry.action === 'workflow.pause') return workflow1.paused ? blocked(entry, 'WORKFLOW_PAUSED') : entry;
    if (entry.action === 'workflow.resume') return workflow1.paused ? entry : blocked(entry, 'WORKFLOW_PAUSED');
    // CP-WF-011: a pause stops new stages from starting, and a cancelled workflow stops all of them
    if (workflow1.paused && entry.action === 'stage.run') return blocked(entry, 'WORKFLOW_PAUSED');
    return entry;
  };
  const packageAction = { action: 'frontend_package.download', available: true };
  const withheld = { action: 'frontend_package.download', available: false, reasonCode: 'PACKAGE_NOT_GENERATED', details: {} };
  return {
    ...allowedActions1,
    allowed: [...(allowedActions1.allowed ?? []).map(decide), ...(workflowId === 'WF-b2' ? [packageAction] : [])],
    unavailable: [...(allowedActions1.unavailable ?? []).map(decide), ...(workflowId === 'WF-b2' ? [] : [withheld])],
  };
}
