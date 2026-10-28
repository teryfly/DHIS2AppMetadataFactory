import type { components } from './generated/schema';

type S = components['schemas'];

/** Generic S1 paging envelope (00 doc 6.11 / S5IF-G04). */
export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  nextCursor: string | null;
  items: T[];
}

/** 202 accepted envelope for long-running endpoints (CP-API-012). */
export interface Acceptance {
  stageId?: string | null;
  stepRunId?: string | null;
  executionId?: string | null;
  accepted?: boolean;
  [k: string]: unknown;
}

export type RegressionList = S['RegressionList'];
/** S5IF-BP04 — one section's entries plus S2's own `_section`/`_path` locators. */
export type EntryPage = S['EntryPage'];
export type ProjectView = S['ProjectView'];
export type ProjectPage = S['ProjectPage'];
export type ProjectStageView = S['ProjectStageView'];
export type ProjectSettingsView = S['ProjectSettingsView'];
export type WorkflowView = S['WorkflowView'];
export type StageView = S['StageView'];
export type StageDetailView = S['StageDetailView'];
export type StageRunView = S['StageRunView'];
export type AgentRunView = S['AgentRunView'];
export type StageFeedbackView = S['StageFeedbackView'];
export type StageList = S['StageList'];

export type GateView = S['GateView'];
export type GateSummaryView = S['GateSummaryView'];
export type GateSummaryList = S['GateSummaryList'];
export type GateWarningView = S['GateWarningView'];
export type GateDecisionView = S['GateDecisionView'];
export type GateFoundView = S['GateFoundView'];
export type GateUncertainView = S['GateUncertainView'];
export type GateWillDoView = S['GateWillDoView'];
export type BoundObject = NonNullable<S['GateView']['boundObjects']>[number];
export type ConfirmationItem = NonNullable<S['GateView']['confirmationItems']>[number];
export type ApprovalEntry = NonNullable<S['GateView']['approvals']>[number];
export type WarningDispositionInput = S['WarningDisposition'];

export type QuestionView = S['QuestionView'];
export type SuggestionView = S['SuggestionView'];
export type QuestionSourceView = S['QuestionSourceView'];
export type AnswerQuestionsBody = S['AnswerQuestionsBody'];
export type QuestionnaireAnswerItem = S['QuestionnaireAnswerItem'];
export type QuestionnaireAnswerView = S['QuestionnaireAnswerView'];

export type NotificationView = S['NotificationView'];
export type NotificationPage = S['NotificationPage'];

export type EnvironmentView = S['EnvironmentView'];
export type EnvironmentPage = S['EnvironmentPage'];
export type CapabilityProfileView = S['CapabilityProfileView'];
export type ConnectionCheckView = S['ConnectionCheckView'];
export type ExpiringCredentialList = S['ExpiringCredentialList'];

export type ModelView = S['ModelView'];
export type ModelList = S['ModelList'];
export type RegisterModelRequest = S['RegisterModelRequest'];
export type SetupView = S['SetupView'];
export type ProviderView = S['ProviderView'];
export type SecretView = S['SecretView'];
export type SecretListView = S['SecretListView'];
export type SecretWriteRequest = S['SecretWriteRequest'];
export type SecretDeletedView = S['SecretDeletedView'];
export type ProviderCatalogueEntry = S['ProviderCatalogueEntry'];
export type ProviderCatalogueView = S['ProviderCatalogueView'];
export type SetupCredentialsView = S['SetupCredentialsView'];
export type ProviderList = S['ProviderList'];
export type PolicyView = S['PolicyView'];
export type RelaxPolicyRequest = S['RelaxPolicyRequest'];
export type RelaxationView = S['RelaxationView'];
export type UsageView = S['UsageView'];

export type SystemSettingsView = S['SystemSettingsView'];
export type UploadLimitsView = S['UploadLimitsView'];

export type RunView = S['RunView'];
export type RunList = S['RunList'];
export type RunPage = S['RunPage'];
export type RunDetailView = S['RunDetailView'];
export type RunSummaryView = S['RunSummaryView'];
export type ChangeStatsView = S['ChangeStatsView'];

export type ArtifactView = S['ArtifactView'];
export type ArtifactPage = S['ArtifactPage'];
export type ArtifactDiffView = S['ArtifactDiffView'];
export type Lineage = S['LineageView'];

export type InputView = S['InputView'];
export type LlmLogView = S['LlmLogView'];
export type LlmExchangeSummaryView = S['LlmExchangeSummaryView'];
export type LlmExchangeDetailView = S['LlmExchangeDetailView'];
export type InputPage = S['InputPage'];
export type ColumnMappingView = S['ColumnMappingView'];
export type ColumnMappingProposalView = S['ColumnMappingProposalView'];
export type InputCompletenessView = S['InputCompletenessView'];

export type RegistryEntryView = S['RegistryEntryView'];
export type RegistryPage = S['RegistryPage'];

export type SectionSchemaView = S['SectionSchemaView'];
export type SectionSchemaIndexView = S['SectionSchemaIndexView'];

export type BlueprintDiffView = S['BlueprintDiffView'];
export type BlueprintRefView = S['BlueprintRefView'];

export type NrrItemView = S['NrrItemView'];
export type NrrPage = S['NrrPage'];

export type ValidationIssuePage = S['ValidationIssuePage'];
export type ValidationIssue = NonNullable<S['ValidationIssuePage']['items']>[number];

export type ChangeRequestView = S['ChangeRequestView'];
export type ChangeRequestList = S['ChangeRequestList'];
export type ChangeRequestImpactView = S['ChangeRequestImpactView'];

export type AuditRecordView = S['AuditRecordView'];
export type AuditPage = S['AuditPage'];
export type CommitTraceView = S['CommitTraceView'];

export type TargetEnvironmentView = S['TargetEnvironmentView'];
export type TargetEnvironmentListView = S['TargetEnvironmentListView'];

export type CreateProjectBody = S['CreateProjectBody'];
export type UpdateProjectBody = S['UpdateProjectBody'];
export type AnswerQuestionBody = S['AnswerQuestionBody'];
export type ApproveGateBody = S['ApproveGateBody'];
export type RejectGateBody = S['RejectGateBody'];
export type StagePatchBody = S['StagePatchBody'];
export type ManualCheckBody = S['ManualCheckBody'];
export type EmergencyLockRequest = S['EmergencyLockRequest'];
export type CreateRunBody = S['CreateRunBody'];
export type SystemSettingsBody = S['SystemSettingsBody'];
export type ProjectSettingsBody = S['ProjectSettingsBody'];
export type CreateEnvironmentRequest = S['CreateEnvironmentRequest'];
export type UpdateEnvironmentRequest = S['UpdateEnvironmentRequest'];
export type CredentialSpec = S['CredentialSpec'];
export type ProtectionSpec = S['ProtectionSpec'];
export type CommitWindowSpec = S['CommitWindowSpec'];
export type RegisterProviderRequest = S['RegisterProviderRequest'];
export type PatchProviderRequest = S['PatchProviderRequest'];
export type PatchModelRequest = S['PatchModelRequest'];
export type TestConnectionRequest = S['TestConnectionRequest'];
export type CreateChangeRequestBody = S['CreateChangeRequestBody'];
export type ParseInputRequest = S['ParseInputRequest'];

export type BlueprintDiffViewExt = S['BlueprintDiffView'];
export type DiffObject = NonNullable<S['BlueprintDiffView']['objects']>[number];

export type ExecutionView = S['ExecutionView'];
export type ExecutionReportView = S['ExecutionReportView'];
export type ReconcileView = S['ReconcileView'];

export type BindingPage = S['BindingPage'];
export type BindingView = S['BindingView'];
export type BindingSetRequest = S['BindingSetRequest'];
export type CandidatePage = S['CandidatePage'];
export type CandidateView = NonNullable<S['CandidatePage']['items']>[number];

export type GateExplanationView = S['GateExplanationView'];
export type UpgradeModeView = S['UpgradeModeView'];

export type PromptList = S['PromptList'];
export type PromptView = NonNullable<S['PromptList']['items']>[number];
export type EvaluationList = S['EvaluationList'];
export type EvaluationView = NonNullable<S['EvaluationList']['items']>[number];
export type BudgetView = S['BudgetView'];
export type AgentListView = S['AgentListView'];

export type ManagedScopePage = S['ManagedScopePage'];
export type ManagedObjectView = NonNullable<S['ManagedScopePage']['items']>[number];
export type DecisionList = S['DecisionList'];
export type DecisionView = NonNullable<S['DecisionList']['items']>[number];
export type UidPoolView = S['UidPoolView'];
export type EnvironmentProtectionView = S['EnvironmentProtectionView'];
export type ProjectPolicyView = S['ProjectPolicyView'];
export type PatchSubmitView = S['PatchSubmitView'];
export type VersionPage = S['VersionPage'];
export type VersionView = NonNullable<S['VersionPage']['items']>[number];

// ---- M4 auth & permissions
export type LoginRequest = S['LoginRequest'];
export type SessionView = S['SessionView'];
export type OidcRequestView = S['OidcRequestView'];
export type OidcExchangeRequest = S['OidcExchangeRequest'];
export type ClearedView = S['ClearedView'];
export type UserList = S['UserList'];
export type UserView = S['UserView'];
export type UserCreateRequest = S['UserCreateRequest'];
export type UserPatchRequest = S['UserPatchRequest'];
export type PasswordSetRequest = S['PasswordSetRequest'];
export type RoleList = S['RoleList'];
export type RoleView = NonNullable<S['RoleList']['items']>[number];
export type RoleAssignmentRequest = S['RoleAssignmentRequest'];
export type RoleAssignmentView = S['RoleAssignmentView'];

// ---- M4 project authorization (FR-SEC-003, FR-SEC-004, CP-WF-020)
export type MemberList = S['MemberList'];
export type MemberView = NonNullable<S['MemberList']['items']>[number];
export type MemberPutRequest = S['MemberPutRequest'];
export type GrantList = S['GrantList'];
export type GrantView = NonNullable<S['GrantList']['items']>[number];
export type GrantCreateRequest = S['GrantCreateRequest'];
export type ExemptionList = S['ExemptionList'];
export type ExemptionView = NonNullable<S['ExemptionList']['items']>[number];
export type ExemptionCreateRequest = S['ExemptionCreateRequest'];
// ---- budget write
export type BudgetSetRequest = S['BudgetSetRequest'];

// ---- M3 stage chat + object graph
export type ChatTranscriptView = S['ChatTranscriptView'];
export type ChatTurnItemView = NonNullable<S['ChatTranscriptView']['turns']>[number];
export type ChatTurnRequest = S['ChatTurnRequest'];
export type ChatTurnView = S['ChatTurnView'];
export type ChatQuestionView = NonNullable<S['ChatTurnView']['questions']>[number];
export type ChatClearedView = S['ChatClearedView'];
export type WorkflowObjectGraphView = S['WorkflowObjectGraphView'];
export type GraphNodeView = NonNullable<S['WorkflowObjectGraphView']['nodes']>[number];
export type GraphEdgeView = NonNullable<S['WorkflowObjectGraphView']['edges']>[number];
export type UnresolvedReferenceView = S['UnresolvedReferenceView'];
export type PackageFileList = S['PackageFilesView'];
export type FrontendPackageFilesView = S['FrontendPackageFilesView'];
export type FrontendPackageFileView = S['FrontendPackageFileView'];
export type PackageFileView = NonNullable<S['PackageFilesView']['files']>[number];

/** Loose record for raw artifact content and other dynamic JSON payloads. */
export type Json = Record<string, unknown> | unknown[] | string | number | boolean | null;