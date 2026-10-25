/*
 * Thin alias layer over the OpenAPI-generated component schemas so feature code reads clearly
 * (`ProjectView`) while staying pinned to the contract (UI-APP-007). Nothing here is hand-modeled.
 */
import type { components, operations } from './generated/schema';

type Schemas = components['schemas'];

export type ActionEntry = Schemas['ActionEntry'];
export type AllowedActionsView = Schemas['AllowedActionsView'];
export type WorkflowView = Schemas['WorkflowView'];
export type StageView = Schemas['StageView'];
export type ProjectStageView = Schemas['ProjectStageView'];
export type StageDetailView = Schemas['StageDetailView'];
export type Acceptance = Schemas['AcceptanceView'];
export type StageFeedbackView = Schemas['StageFeedbackView'];
export type InputCompletenessView = Schemas['InputCompletenessView'];
export type ProjectView = Schemas['ProjectView'];
export type ProjectPage = Schemas['ProjectPage'];
export type GateView = Schemas['GateView'];
export type GateExplanationView = Schemas['GateExplanationView'];
export type GateSummaryView = Schemas['GateSummaryView'];
export type GateSummaryList = Schemas['GateSummaryList'];
export type DecisionList = Schemas['DecisionList'];
export type DecisionView = Schemas['DecisionView'];
export type GateWarningView = Schemas['GateWarningView'];
export type ConfirmationItemView = Schemas['ConfirmationItemView'];
export type ApprovalView = Schemas['ApprovalView'];
export type ApprovalConfirmation = Schemas['ApprovalConfirmation'];
export type WarningDisposition = Schemas['WarningDisposition'];
export type BoundObjectView = Schemas['BoundObjectView'];
export type QuestionView = Schemas['QuestionView'];
export type SuggestionView = Schemas['SuggestionView'];
export type QuestionAnswerView = Schemas['QuestionAnswerView'];
export type AnswerQuestionsBody = Schemas['AnswerQuestionsBody'];
export type QuestionnaireAnswerView = Schemas['QuestionnaireAnswerView'];
export type NotificationView = Schemas['NotificationView'];
export type NotificationPage = Schemas['NotificationPage'];
export type EnvironmentView = Schemas['EnvironmentView'];
export type PromptList = Schemas['PromptList'];
export type EvaluationList = Schemas['EvaluationList'];
export type RegressionList = Schemas['RegressionList'];
export type ActivatePromptRequest = Schemas['ActivatePromptRequest'];

export type EnvironmentPage = Schemas['EnvironmentPage'];
export type EnvironmentProtectionView = Schemas['EnvironmentProtectionView'];
export type ProtectionSpec = Schemas['ProtectionSpec'];
export type CommitWindowSpec = Schemas['CommitWindowSpec'];
export type CapabilityProfileView = Schemas['CapabilityProfileView'];
export type ConnectionCheckView = Schemas['ConnectionCheckView'];
export type ProbeResultView = Schemas['ProbeResultView'];
export type DisableResultView = Schemas['DisableResultView'];
export type ExpiringCredentialList = Schemas['ExpiringCredentialList'];
export type ModelView = Schemas['ModelView'];
export type PatchModelRequest = Schemas['PatchModelRequest'];
export type ModelList = Schemas['ModelList'];
export type SetupView = Schemas['SetupView'];
export type SetupEnvironmentItem = Schemas['SetupEnvironmentItem'];
export type ProviderView = Schemas['ProviderView'];
/** S5IF-GOV20 — the answer to one reachability probe of a provider's address. */
export type ProviderHealthView = Schemas['ProviderHealthView'];
export type SecretView = Schemas['SecretView'];
export type SecretListView = Schemas['SecretListView'];
export type SecretWriteRequest = Schemas['SecretWriteRequest'];
export type ProviderCatalogueEntry = Schemas['ProviderCatalogueEntry'];
export type ProviderCatalogueView = Schemas['ProviderCatalogueView'];
export type AgentListView = Schemas['AgentListView'];
export type AgentSpecView = Schemas['AgentSpecView'];
export type SetupCredentialsView = Schemas['SetupCredentialsView'];
export type SetupRefHolderView = Schemas['SetupRefHolderView'];
export type ProviderList = Schemas['ProviderList'];
export type PolicyView = Schemas['PolicyView'];
export type RelaxPolicyRequest = Schemas['RelaxPolicyRequest'];
export type RelaxationView = Schemas['RelaxationView'];
export type UsageView = Schemas['UsageView'];
export type SystemSettingsView = Schemas['SystemSettingsView'];
export type SystemSettingItem = Schemas['SystemSettingItem'];
export type InputView = Schemas['InputView'];
export type InputPage = Schemas['InputPage'];
export type LlmLogView = Schemas['LlmLogView'];
export type LlmExchangeSummaryView = Schemas['LlmExchangeSummaryView'];
export type LlmExchangeDetailView = Schemas['LlmExchangeDetailView'];
export type NrrPage = Schemas['NrrPage'];
export type NrrItemView = Schemas['NrrItemView'];
export type RegistryPage = Schemas['RegistryPage'];
export type RegistryEntryView = Schemas['RegistryEntryView'];
export type ValidationIssuePage = Schemas['ValidationIssuePage'];
export type IssueView = Schemas['IssueView'];
export type ArtifactView = Schemas['ArtifactView'];
export type ArtifactPage = Schemas['ArtifactPage'];
export type ArtifactDiffView = Schemas['ArtifactDiffView'];
export type LineageView = Schemas['LineageView'];
export type ReproduceView = Schemas['ReproduceView'];
export type BlueprintDiffView = Schemas['BlueprintDiffView'];
export type DiffObjectView = Schemas['DiffObjectView'];
export type BlueprintVersionPage = Schemas['VersionPage'];
export type BlueprintVersionView = Schemas['VersionView'];
export type EntryPage = Schemas['EntryPage'];
export type RunView = Schemas['RunView'];
export type RunList = Schemas['RunList'];
export type RunSummaryView = Schemas['RunSummaryView'];
export type BindingView = Schemas['BindingView'];
export type BindingPage = Schemas['BindingPage'];
export type CandidateView = Schemas['CandidateView'];
export type CandidatePage = Schemas['CandidatePage'];
export type PackageFilesView = Schemas['PackageFilesView'];
export type PackageFileView = Schemas['PackageFileView'];
export type FrontendPackageFilesView = Schemas['FrontendPackageFilesView'];
export type FrontendPackageFileView = Schemas['FrontendPackageFileView'];
export type WorkflowObjectGraphView = Schemas['WorkflowObjectGraphView'];
export type GraphNodeView = Schemas['GraphNodeView'];
export type GraphEdgeView = Schemas['GraphEdgeView'];
export type GraphFiltersView = Schemas['GraphFiltersView'];
export type UnresolvedReferenceView = Schemas['UnresolvedReferenceView'];
export type DeploymentRunPage = Schemas['RunPage'];
export type DraftView = Schemas['DraftView'];
export type ActiveDraftView = Schemas['ActiveDraftView'];
export type SectionSchemaView = Schemas['SectionSchemaView'];
export type SectionSchemaIndexView = Schemas['SectionSchemaIndexView'];
export type PatchSubmitView = Schemas['PatchSubmitView'];
export type ManualCheckView = Schemas['ManualCheckView'];
export type ReconcileView = Schemas['ReconcileView'];
export type SubmitApprovalView = Schemas['SubmitApprovalView'];
export type StageRunView = Schemas['StageRunView'];
export type ColumnMappingView = Schemas['ColumnMappingView'];
export type ColumnMappingProposalView = Schemas['ColumnMappingProposalView'];
export type AgentProgressView = Schemas['AgentProgressView'];
export type AgentRunView = Schemas['AgentRunView'];
export type ExplainResultView = Schemas['ExplainResultView'];
export type ParseOptions = Schemas['ParseOptions'];
export type UploadLimitsView = Schemas['UploadLimitsView'];
export type ExecutionReportView = Schemas['ExecutionReportView'];
export type ExecutionView = Schemas['ExecutionView'];
export type CommitTraceView = Schemas['CommitTraceView'];
export type AuditPage = Schemas['AuditPage'];
export type AuditRecordView = Schemas['AuditRecordView'];
export type ChangeRequestView = Schemas['ChangeRequestView'];
export type ChangeRequestImpactView = Schemas['ChangeRequestImpactView'];
export type TargetEnvironmentView = Schemas['TargetEnvironmentView'];

// Request bodies (typed inputs the wizard / forms submit).
export type CreateProjectBody = Schemas['CreateProjectBody'];
export type CreateChangeRequestBody = Schemas['CreateChangeRequestBody'];
export type TargetEnvironmentBody = Schemas['TargetEnvironmentBody'];
export type CreateEnvironmentRequest = Schemas['CreateEnvironmentRequest'];
export type UpdateEnvironmentRequest = Schemas['UpdateEnvironmentRequest'];
export type CredentialSpec = Schemas['CredentialSpec'];
export type RegisterProviderRequest = Schemas['RegisterProviderRequest'];
export type PatchProviderRequest = Schemas['PatchProviderRequest'];
export type RegisterModelRequest = Schemas['RegisterModelRequest'];
export type SystemSettingsBody = Schemas['SystemSettingsBody'];
export type ApproveGateBody = Schemas['ApproveGateBody'];
export type RejectGateBody = Schemas['RejectGateBody'];
export type AnswerQuestionBody = Schemas['AnswerQuestionBody'];
export type StagePatchBody = Schemas['StagePatchBody'];
export type ManualCheckBody = Schemas['ManualCheckBody'];

/** Convenience: a successful response type for an operation id. */
export type Resp<T extends keyof operations> = operations[T] extends {
  responses: { 200: { content: { 'application/json': infer R } } };
}
  ? R
  : never;

/** M4 identity and permissions (CP-IAM-002, UI-AUTH-001/003). */
export type SessionView = Schemas['SessionView'];
export type LoginBody = Schemas['LoginRequest'];
export type UserView = Schemas['UserView'];
export type UserList = Schemas['UserList'];
export type RoleView = Schemas['RoleView'];
export type RoleList = Schemas['RoleList'];
export type RoleAssignmentView = Schemas['RoleAssignmentView'];
export type MemberView = Schemas['MemberView'];
export type QuestionPage = Schemas['QuestionPage'];
export type MemberList = Schemas['MemberList'];
export type ProjectSettingsView = Schemas['ProjectSettingsView'];
export type ProjectSettingsBody = Schemas['ProjectSettingsBody'];
export type TargetEnvironmentListView = Schemas['TargetEnvironmentListView'];
export type TargetEnvironmentReplaceView = Schemas['TargetEnvironmentReplaceView'];
export type UidPoolView = Schemas['UidPoolView'];
export type UpgradeModeView = Schemas['UpgradeModeView'];
export type GrantView = Schemas['GrantView'];
export type GrantList = Schemas['GrantList'];
export type ExemptionView = Schemas['ExemptionView'];
export type ExemptionList = Schemas['ExemptionList'];
export type ClearedView = Schemas['ClearedView'];

/** AI-COST / AI-GOV-007 (UI-ADM-005). */
export type BudgetView = Schemas['BudgetView'];
export type BudgetSetRequest = Schemas['BudgetSetRequest'];
export type UsageItemView = Schemas['UsageItemView'];

/** In-stage dialogue (AI-CHAT-001~003, UI-WB-010). */
export type ChatTurnView = Schemas['ChatTurnView'];
export type ChatTranscriptView = Schemas['ChatTranscriptView'];
export type ChatClearedView = Schemas['ChatClearedView'];
export type ChatQuestionView = Schemas['ChatQuestionView'];
export type StageChatContext = Schemas['StageContextView'];

export type { operations, components, paths } from './generated/schema';
