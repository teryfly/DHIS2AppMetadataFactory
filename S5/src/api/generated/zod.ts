/* eslint-disable */
// AUTO-GENERATED from contract/openapi.json by scripts/codegen-zod.ts.
// Do not edit by hand — regenerate with `npm run codegen:zod`.
import { z } from 'zod';

export const AcceptanceView = z.object({
      accepted: z.boolean().optional(),
      stageId: z.string().nullish(),
      stepRunId: z.string(),
    });

export const ActionEntry = z.object({
      action: z.string(),
      available: z.boolean(),
      details: z.record(z.string(), z.unknown()).optional(),
      reasonCode: z.string().nullish(),
      targetId: z.string().nullish(),
    });

export const ActivatePromptRequest = z.object({
      agentType: z.string(),
      modelList: z.array(z.string()).optional(),
      promptName: z.string(),
      promptVersion: z.number().int(),
    });

export const ActivationList = z.object({
      items: z.array(z.lazy(() => ActivationView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ActivationView = z.object({
      activatedBy: z.string().nullish(),
      agentType: z.string().nullish(),
      evalRunId: z.string().nullish(),
      modelList: z.array(z.string()).optional(),
      name: z.string().nullish(),
      promptVersionId: z.string().nullish(),
      regressionPassedRef: z.string().nullish(),
      version: z.number().int().nullish(),
    });

export const ActiveDraftView = z.object({
      baseVersion: z.string().nullish(),
      contentHash: z.string().nullish(),
      draftId: z.string(),
      stage: z.string().nullish(),
      stageScope: z.array(z.string()).optional(),
    });

export const AgentListView = z.object({
      items: z.array(z.lazy(() => AgentSpecView)).optional(),
      milestone: z.string().nullish(),
    });

export const AgentProgressView = z.object({
      completedFraction: z.number().nullish(),
      message: z.string().nullish(),
      step: z.string().nullish(),
    });

export const AgentRunView = z.object({
      agentOutputArtifactId: z.string().nullish(),
      budgetStatus: z.record(z.string(), z.unknown()).optional(),
      confidence: z.number().nullish(),
      denialReasons: z.array(z.string()).optional(),
      issues: z.array(z.lazy(() => IssueView)).optional(),
      patchIds: z.array(z.string()).optional(),
      progress: z.lazy(() => AgentProgressView).nullish(),
      questions: z.array(z.lazy(() => QuestionView)).optional(),
      rejectedArtifacts: z.array(z.record(z.string(), z.unknown())).optional(),
      runId: z.string(),
      stage: z.string().nullish(),
      stageId: z.string().nullish(),
      status: z.string().optional(),
      summary: z.string().nullish(),
      workflowId: z.string().nullish(),
    });

export const AgentSpecView = z.object({
      agentType: z.string().nullish(),
      enabled: z.boolean().nullish(),
      milestone: z.string().nullish(),
      patchMode: z.string().nullish(),
      perPage: z.boolean().nullish(),
      promptName: z.string().nullish(),
      stage: z.string().nullish(),
    });

export const AllowedActionsView = z.object({
      allowed: z.array(z.lazy(() => ActionEntry)).optional(),
      evaluatedAt: z.string().nullish(),
      scope: z.string().optional(),
      unavailable: z.array(z.lazy(() => ActionEntry)).optional(),
      workflowId: z.string().nullish(),
    });

export const AnswerQuestionBody = z.object({
      customText: z.string().nullish(),
      suggestionId: z.string().nullish(),
      suggestionIds: z.array(z.string()).optional(),
    });

export const AnswerQuestionsBody = z.object({
      answers: z.array(z.lazy(() => QuestionnaireAnswerItem)).min(1).max(200),
    });

export const AnswerView = z.object({
      questionId: z.string(),
      resumed: z.boolean().optional(),
      status: z.string(),
    });

export const ApprovalConfirmation = z.object({
      confirmed: z.boolean().optional(),
      id: z.string().nullish(),
      note: z.string().nullish(),
    });

export const ApprovalDecision = z.object({
      approverName: z.string().nullish(),
      comment: z.string().nullish(),
      createdAt: z.string().nullish(),
      decision: z.string(),
    });

export const ApprovalMismatch = z.object({
      actualHash: z.string().nullable(),
      expectedHash: z.string(),
      problem: z.enum(["NOT_PRESENTED", "STALE"] as const).optional(),
      refId: z.string(),
      refType: z.string(),
    });

export const ApprovalSlot = z.object({
      approvals: z.number().int().optional(),
      deployGate: z.string().nullish(),
      packageGate: z.string().nullish(),
      requiredApprovals: z.number().int().optional(),
    });

export const ApprovalView = z.object({
      approverName: z.string().nullish(),
      comment: z.string().nullish(),
      createdAt: z.string().nullish(),
      decision: z.string().nullish(),
      id: z.string(),
    });

export const ApproveGateBody = z.object({
      comment: z.string().nullish(),
      confirmations: z.array(z.lazy(() => ApprovalConfirmation)).optional(),
      gateType: z.string().optional(),
      seenBoundHashes: z.array(z.record(z.string(), z.unknown())).optional(),
      warningDispositions: z.array(z.lazy(() => WarningDisposition)).optional(),
    });

export const ArtifactDiffView = z.object({
      aId: z.string().nullish(),
      bId: z.string().nullish(),
      changed: z.boolean().optional(),
      kind: z.string().nullish(),
      lines: z.array(z.record(z.string(), z.unknown())).optional(),
    });

export const ArtifactPage = z.object({
      items: z.array(z.lazy(() => ArtifactView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ArtifactPurgeView = z.object({
      artifactId: z.string(),
      contentPurged: z.boolean(),
    });

export const ArtifactSlot = z.object({
      artifactId: z.string().nullish(),
      contentPurged: z.boolean().optional(),
      status: z.string().nullish(),
    });

export const ArtifactView = z.object({
      contentHash: z.string().nullish(),
      contentPurged: z.boolean().optional(),
      createdAt: z.string().nullish(),
      creatorOperator: z.string().nullish(),
      creatorSubsystem: z.string().nullish(),
      dataClassification: z.string().nullish(),
      id: z.string(),
      mediaType: z.string().nullish(),
      prevVersionId: z.string().nullish(),
      projectId: z.string().nullish(),
      size: z.number().int().optional(),
      sourceArtifactIds: z.array(z.string()).optional(),
      status: z.string().nullish(),
      type: z.string().nullish(),
      version: z.union([z.number().int(), z.string()]).nullish(),
      workflowId: z.string().nullish(),
    });

export const AuditPage = z.object({
      items: z.array(z.lazy(() => AuditRecordView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const AuditRecordView = z.object({
      after: z.record(z.string(), z.unknown()).nullish(),
      before: z.record(z.string(), z.unknown()).nullish(),
      correlationId: z.string().nullish(),
      eventType: z.string().nullish(),
      hash: z.string().nullish(),
      id: z.string(),
      objectId: z.string().nullish(),
      objectType: z.string().nullish(),
      occurredAt: z.string().nullish(),
      operatorName: z.string().nullish(),
      operatorType: z.string().nullish(),
      prevHash: z.string().nullish(),
      projectId: z.string().nullish(),
      redacted: z.boolean().optional(),
      sequence: z.number().int().nullish(),
      sourceSubsystem: z.string().nullish(),
    });

export const AuditVerifyBody = z.object({
      firstSequence: z.number().int().nullish(),
      lastSequence: z.number().int().nullish(),
    });

export const AuditVerifyView = z.object({
      brokenAt: z.number().int().nullish(),
      firstSequence: z.number().int().nullish(),
      lastSequence: z.number().int().nullish(),
      message: z.string().nullish(),
      recordCount: z.number().int().optional(),
      verified: z.boolean(),
    });

export const BindingPage = z.object({
      items: z.array(z.lazy(() => BindingView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const BindingSetRequest = z.object({
      note: z.string(),
      uid: z.string(),
    });

export const BindingView = z.object({
      blueprintVersion: z.string().nullish(),
      businessKey: z.string(),
      discoveryResultId: z.string().nullish(),
      environmentKey: z.string().nullish(),
      note: z.string().nullish(),
      observedHash: z.string().nullish(),
      observedLastUpdated: z.string().nullish(),
      pendingReparse: z.boolean().optional(),
      resolution: z.string().nullish(),
      resolvedBy: z.string().nullish(),
      uid: z.string().nullish(),
    });

export const BlueprintDiffView = z.object({
      counts: z.record(z.string(), z.number().int()).optional(),
      environmentKey: z.string().nullish(),
      fromVersion: z.string().nullish(),
      kind: z.string().nullish(),
      objects: z.array(z.lazy(() => DiffObjectView)).optional(),
      projectId: z.string().nullish(),
      toVersion: z.string().nullish(),
    });

export const BlueprintRefView = z.object({
      hash: z.string().nullish(),
      id: z.string(),
      kind: z.string(),
    });

export const Body_upload_input_api_v1_projects__project_id__inputs_post = z.object({
      dataClassification: z.lazy(() => DataClassification),
      file: z.string(),
      mediaType: z.string().nullish(),
      phiDeclared: z.boolean().optional(),
    });

export const BoundObjectView = z.object({
      contentHash: z.string(),
      refId: z.string(),
      refType: z.string(),
    });

export const BudgetSetRequest = z.object({
      currency: z.string().optional(),
      limit: z.number().nullish(),
      thresholds: z.array(z.number()).optional(),
    });

export const BudgetView = z.object({
      currency: z.string().optional(),
      limit: z.number().nullish(),
      projectId: z.string().nullish(),
      spent: z.number().optional(),
      state: z.string().optional(),
      thresholdReached: z.number().nullish(),
    });

export const CandidatePage = z.object({
      businessKey: z.string().nullish(),
      dhis2Type: z.string().nullish(),
      discoveryArtifactId: z.string().nullish(),
      environmentKey: z.string().nullish(),
      items: z.array(z.lazy(() => CandidateView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const CandidateView = z.object({
      code: z.string().nullish(),
      contentHash: z.string().nullish(),
      dhis2Type: z.string().nullish(),
      lastUpdated: z.string().nullish(),
      matching: z.boolean().optional(),
      name: z.string().nullish(),
      uid: z.string().nullish(),
    });

export const CapabilityProfileView = z.object({
      artifactId: z.string().nullish(),
      content: z.record(z.string(), z.unknown()).nullish(),
      contentHash: z.string().nullish(),
      dhis2Version: z.string().nullish(),
      environmentKey: z.string(),
      freshness: z.string().optional(),
      probedAt: z.string().nullish(),
      profileVersion: z.string().nullish(),
      stale: z.boolean().optional(),
      staleReason: z.string().nullish(),
    });

export const ChangeRequestImpactView = z.object({
      affectedBusinessKeys: z.array(z.string()).optional(),
      affectedStages: z.array(z.string()).optional(),
      approvedBy: z.string().nullish(),
      createdAt: z.string().nullish(),
      deletionCandidates: z.array(z.string()).optional(),
      deployedEnvironments: z.array(z.string()).optional(),
      id: z.string(),
      impact: z.lazy(() => ImpactView).optional(),
      impactReportArtifactId: z.string().nullish(),
      projectId: z.string().nullish(),
      reason: z.string().nullish(),
      rejectedReason: z.string().nullish(),
      requestedBy: z.string().nullish(),
      resultBlueprintVersion: z.string().nullish(),
      status: z.string(),
    });

export const ChangeRequestList = z.object({
      items: z.array(z.lazy(() => ChangeRequestView)).optional(),
    });

export const ChangeRequestView = z.object({
      affectedBusinessKeys: z.array(z.string()).optional(),
      affectedStages: z.array(z.string()).optional(),
      approvedBy: z.string().nullish(),
      createdAt: z.string().nullish(),
      deletionCandidates: z.array(z.string()).optional(),
      id: z.string(),
      impactReportArtifactId: z.string().nullish(),
      projectId: z.string().nullish(),
      reason: z.string().nullish(),
      rejectedReason: z.string().nullish(),
      requestedBy: z.string().nullish(),
      resultBlueprintVersion: z.string().nullish(),
      status: z.string(),
    });

export const ChangeStatsView = z.object({
      create: z.number().int().optional(),
      delete: z.number().int().optional(),
      externalReferences: z.number().int().optional(),
      update: z.number().int().optional(),
    });

export const ChatClearedView = z.object({
      cleared: z.boolean().optional(),
      context: z.lazy(() => StageContextView),
      stageId: z.string(),
    });

export const ChatQuestionView = z.object({
      affects: z.array(z.string()).optional(),
      blocking: z.boolean().optional(),
      localId: z.string(),
      severity: z.string().optional(),
      suggestions: z.array(z.lazy(() => SuggestionView)).optional(),
      text: z.string().optional(),
    });

export const ChatTranscriptView = z.object({
      context: z.lazy(() => StageContextView),
      stageId: z.string(),
      turns: z.array(z.lazy(() => ChatTurnItemView)).optional(),
    });

export const ChatTurnItemView = z.object({
      content: z.string().optional(),
      role: z.string(),
    });

export const ChatTurnRequest = z.object({
      message: z.string(),
    });

export const ChatTurnView = z.object({
      answer: z.string().optional(),
      context: z.lazy(() => StageContextView),
      patchIds: z.array(z.string()).optional(),
      questions: z.array(z.lazy(() => ChatQuestionView)).optional(),
      stageId: z.string(),
    });

export const ClearedView = z.object({
      authMode: z.string().nullish(),
      classification: z.string().nullish(),
      cleared: z.boolean().optional(),
      projectId: z.string().nullish(),
      sessionId: z.string().nullish(),
      userId: z.string().nullish(),
      waiver: z.string().nullish(),
    });

export const ColumnMappingProposalView = z.object({
      affectedKeys: z.array(z.string()).optional(),
      columnMapping: z.record(z.string(), z.string()).optional(),
      headers: z.array(z.string()).optional(),
      question: z.string().nullish(),
      questionId: z.string().nullish(),
      sheet: z.string().nullish(),
      suggestions: z.array(z.record(z.string(), z.unknown())).optional(),
    });

export const ColumnMappingView = z.object({
      artifactId: z.string(),
      proposals: z.array(z.lazy(() => ColumnMappingProposalView)).optional(),
      tablePlans: z.array(z.record(z.string(), z.unknown())).nullish(),
      workflowId: z.string().nullish(),
    });

export const CommitTraceView = z.object({
      approvals: z.array(z.lazy(() => TraceApprovalView)).optional(),
      auditTrail: z.array(z.lazy(() => AuditRecordView)).optional(),
      blueprintVersion: z.string().nullish(),
      commitOperator: z.string().nullish(),
      environmentKey: z.string().nullish(),
      execution: z.lazy(() => ExecutionView).nullish(),
      executionId: z.string().nullish(),
      gateId: z.string().nullish(),
      gateStatus: z.string().nullish(),
      importReportArtifactId: z.string().nullish(),
      packageArtifactId: z.string().nullish(),
      payloadArtifactId: z.string().nullish(),
      payloadHash: z.string().nullish(),
      validateOperator: z.string().nullish(),
    });

export const CommitWindow = z.object({
      days: z.array(z.number().int()),
      end: z.string(),
      start: z.string(),
    });

export const CommitWindowSlot = z.object({
      emergencyLocked: z.boolean().optional(),
      timezone: z.string().optional(),
      windows: z.array(z.record(z.string(), z.unknown())).optional(),
      withinWindow: z.boolean().optional(),
    });

export const CommitWindowSpec = z.object({
      days: z.array(z.number().int()).min(1).optional(),
      end: z.string().optional(),
      note: z.string().nullish(),
      start: z.string().optional(),
    });

export const CommitWindowView = z.object({
      days: z.array(z.number().int()).optional(),
      end: z.string().nullish(),
      start: z.string().nullish(),
    });

export const ConfirmationItemView = z.object({
      detail: z.record(z.string(), z.unknown()).optional(),
      id: z.string(),
      kind: z.string().nullish(),
      label: z.string().nullish(),
      required: z.boolean().optional(),
    });

export const ConnectionCheckItemView = z.object({
      detail: z.string().nullish(),
      name: z.string().nullish(),
      state: z.string().nullish(),
    });

export const ConnectionCheckView = z.object({
      capabilityProfileArtifactId: z.string().nullish(),
      checks: z.array(z.lazy(() => ConnectionCheckItemView)).optional(),
      dhis2Version: z.string().nullish(),
      environmentKey: z.string().nullish(),
      message: z.string().nullish(),
      missingAuthorities: z.record(z.string(), z.array(z.string())).optional(),
      ok: z.boolean().optional(),
      requiredScopes: z.array(z.string()).optional(),
      result: z.string().nullish(),
      testedAt: z.string().nullish(),
    });

export const ConnectionSlot = z.object({
      lastTestedAt: z.string().nullish(),
      missingAuthorities: z.record(z.string(), z.array(z.string())).optional(),
      ok: z.boolean().optional(),
      result: z.string().nullish(),
    });

export const CreateChangeRequestBody = z.object({
      affectedBusinessKeys: z.array(z.string()).optional(),
      deletionCandidates: z.array(z.string()).optional(),
      reason: z.string(),
    });

export const CreateEnvironmentRequest = z.object({
      authMode: z.enum(["PAT", "BASIC"] as const).optional(),
      baseUrl: z.string(),
      credentials: z.array(z.lazy(() => CredentialSpec)).optional(),
      environmentClass: z.enum(["DEV", "TEST", "UAT", "PRODUCTION"] as const).optional(),
      environmentKey: z.string(),
      llmDataPolicy: z.enum(["LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL", "UNRESTRICTED"] as const).optional(),
      name: z.string().nullish(),
      notes: z.string().nullish(),
      protection: z.lazy(() => ProtectionSpec).nullish(),
      timeoutSeconds: z.number().int().optional(),
      tlsRequired: z.boolean().optional(),
      versionConstraint: z.string().nullish(),
    });

export const CreateProjectBody = z.object({
      codePrefix: z.string().optional(),
      defaultLanguage: z.string().optional(),
      deliveryMode: z.string().optional(),
      description: z.string().optional(),
      name: z.string(),
      ownerName: z.string(),
      projectType: z.string().optional(),
      targetEnvironments: z.array(z.lazy(() => TargetEnvironmentBody)).optional(),
      targets: z.array(z.lazy(() => TargetEnvironmentBody)).nullish(),
      translationLanguages: z.array(z.string()).optional(),
      versionConstraints: z.string().optional(),
    });

export const CreateRunBody = z.object({
      environmentKey: z.string(),
    });

export const CreateRunView = z.object({
      run: z.lazy(() => RunView),
      stages: z.array(z.string()).optional(),
    });

export const CredentialSpec = z.object({
      label: z.string().nullish(),
      scope: z.enum(["discovery", "import", "tracker", "datastore", "apps", "maintenance"] as const),
      secretRef: z.string(),
    });

export const CredentialView = z.object({
      expiresAt: z.string().nullish(),
      label: z.string().nullish(),
      scope: z.string(),
      secretRef: z.string(),
    });

export const DataClassification = z.enum(["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED", "PHI_PII"] as const);

export const DecisionList = z.object({
      items: z.array(z.lazy(() => DecisionView)).optional(),
    });

export const DecisionView = z.object({
      affectedArtifacts: z.array(z.string()).optional(),
      alternatives: z.array(z.record(z.string(), z.unknown())).optional(),
      confidence: z.number().nullish(),
      confirmation: z.record(z.string(), z.unknown()).optional(),
      decisionId: z.string(),
      impactLevel: z.string().nullish(),
      rationale: z.string().nullish(),
      risks: z.array(z.record(z.string(), z.unknown())).optional(),
      selected: z.record(z.string(), z.unknown()).optional(),
      subject: z.record(z.string(), z.unknown()).optional(),
      type: z.string().nullish(),
    });

export const DeleteAcceptedView = z.object({
      already: z.boolean().optional(),
      deleted: z.boolean().optional(),
      deletedAt: z.string().nullish(),
      projectId: z.string(),
      requires: z.array(z.string()).optional(),
      status: z.string().nullish(),
    });

export const DiffObjectView = z.object({
      businessKey: z.string(),
      changes: z.array(z.record(z.string(), z.unknown())).optional(),
      dhis2Type: z.string().nullish(),
      disposition: z.string().nullish(),
      section: z.string().nullish(),
      state: z.string().nullish(),
      uid: z.string().nullish(),
    });

export const DisableEnvironmentRequest = z.object({
      reason: z.string().nullish(),
    });

export const DisableResultView = z.object({
      environmentKey: z.string(),
      status: z.string().optional(),
    });

export const DiscoveryResultPage = z.object({
      items: z.array(z.lazy(() => DiscoveryResultView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const DiscoveryResultView = z.object({
      artifactId: z.string(),
      blockingIssues: z.array(z.record(z.string(), z.unknown())).optional(),
      contentHash: z.string().nullish(),
      discoveredAt: z.string().nullish(),
      objectCount: z.number().int().optional(),
      projectId: z.string().nullish(),
      scope: z.string().nullish(),
      version: z.string().nullish(),
    });

export const DraftView = z.object({
      baseVersion: z.string().nullish(),
      changeRequestId: z.string().nullish(),
      contentHash: z.string().nullish(),
      document: z.record(z.string(), z.unknown()).nullish(),
      draftId: z.string(),
      openPatchCount: z.number().int().optional(),
      promotable: z.boolean().nullish(),
      stage: z.string().nullish(),
      stageScope: z.array(z.string()).optional(),
      status: z.string().nullish(),
    });

export const DriftSlot = z.object({
      checkedAt: z.string().nullish(),
      drifted: z.boolean().optional(),
      objects: z.array(z.record(z.string(), z.unknown())).optional(),
      runId: z.string().nullish(),
    });

export const EffectivePolicyView = z.object({
      allowed: z.record(z.string(), z.array(z.string())).optional(),
      sources: z.array(z.record(z.string(), z.unknown())).optional(),
      warnings: z.array(z.record(z.string(), z.unknown())).optional(),
    });

export const EmergencyLockRequest = z.object({
      locked: z.boolean(),
      reason: z.string().nullish(),
    });

export const EmergencyLockView = z.object({
      emergencyLocked: z.boolean(),
      environmentKey: z.string(),
      lockChangedAt: z.string().nullish(),
      lockChangedBy: z.string().nullish(),
      lockReason: z.string().nullish(),
    });

export const EntryPage = z.object({
      entries: z.array(z.record(z.string(), z.unknown())).optional(),
      notFound: z.array(z.string()).optional(),
      projectId: z.string().nullish(),
      source: z.string().optional(),
      version: z.string().nullish(),
    });

export const EnvironmentPage = z.object({
      items: z.array(z.lazy(() => EnvironmentView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const EnvironmentProtectionView = z.object({
      allowsFixtureImport: z.boolean().nullish(),
      commitWindows: z.array(z.lazy(() => CommitWindowView)).optional(),
      dataExportPolicy: z.record(z.string(), z.unknown()).optional(),
      deletionPolicy: z.string().nullish(),
      emergencyLocked: z.boolean().optional(),
      environmentClass: z.string().optional(),
      environmentKey: z.string(),
      llmDataPolicy: z.string().nullish(),
      lockChangedAt: z.string().nullish(),
      lockChangedBy: z.string().nullish(),
      lockReason: z.string().nullish(),
      operatorRole: z.string().nullish(),
      requiredApprovals: z.number().int().nullish(),
      timezone: z.string().optional(),
      withinCommitWindow: z.boolean().optional(),
    });

export const EnvironmentView = z.object({
      authMode: z.string().optional(),
      baseUrl: z.string().nullish(),
      createdAt: z.string().nullish(),
      credentials: z.array(z.lazy(() => CredentialView)).optional(),
      dhis2Version: z.string().nullish(),
      environmentClass: z.string().optional(),
      environmentKey: z.string(),
      llmDataPolicy: z.string().optional(),
      name: z.string().nullish(),
      notes: z.string().nullish(),
      secretRefs: z.record(z.string(), z.string()).optional(),
      status: z.string().optional(),
      timeoutSeconds: z.number().int().optional(),
      tlsRequired: z.boolean().optional(),
      updatedAt: z.string().nullish(),
      versionConstraint: z.string().nullish(),
    });

export const ErrorDetails = z.object({
      actualType: z.string().optional(),
      allowedTypes: z.array(z.string()).optional(),
      artifactId: z.string().optional(),
      blueprintVersion: z.string().optional(),
      capability: z.string().optional(),
      changeRequestId: z.string().optional(),
      currentState: z.string().optional(),
      currentTime: z.string().optional(),
      environmentKey: z.string().optional(),
      environmentTimezone: z.string().optional(),
      executionId: z.string().optional(),
      expectedType: z.string().optional(),
      feature: z.string().optional(),
      fields: z.array(z.lazy(() => ValidationErrorField)).optional(),
      filename: z.string().optional(),
      gateId: z.string().optional(),
      header: z.string().optional(),
      key: z.string().optional(),
      known: z.array(z.string()).optional(),
      lockChangedAt: z.string().optional(),
      lockReason: z.string().optional(),
      maxBytes: z.number().int().optional(),
      mediaType: z.string().optional(),
      milestone: z.string().optional(),
      mismatches: z.array(z.lazy(() => ApprovalMismatch)).optional(),
      projectId: z.string().optional(),
      providerErrorCode: z.string().optional(),
      questionId: z.string().optional(),
      reason: z.enum(["SIZE", "TYPE", "NOT_GENERATED", "NOT_EXIST"] as const).optional(),
      requiredEvidence: z.array(z.string()).optional(),
      requiredStage: z.string().optional(),
      requiredStates: z.array(z.string()).optional(),
      requires: z.string().optional(),
      retryable: z.boolean().optional(),
      runId: z.string().optional(),
      scope: z.string().optional(),
      sizeBytes: z.number().int().optional(),
      stageId: z.string().optional(),
      status: z.string().optional(),
      suggestionId: z.string().optional(),
      targetId: z.string().optional(),
      violation: z.string().optional(),
      windows: z.array(z.lazy(() => CommitWindow)).optional(),
      workflowId: z.string().optional(),
    });

export const EvalCaseView = z.object({
      caseId: z.string().nullish(),
      diff: z.record(z.string(), z.unknown()).nullish(),
      kind: z.string().nullish(),
      passed: z.boolean().nullish(),
      score: z.number().nullish(),
    });

export const EvaluationList = z.object({
      items: z.array(z.lazy(() => EvaluationView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const EvaluationView = z.object({
      agentType: z.string().nullish(),
      caseCount: z.number().int().nullish(),
      cases: z.array(z.lazy(() => EvalCaseView)).nullish(),
      evalSetVersion: z.string().nullish(),
      id: z.string().nullish(),
      injectionCases: z.number().int().nullish(),
      injectionPassed: z.number().int().nullish(),
      model: z.string().nullish(),
      passed: z.boolean().nullish(),
      prompt: z.string().nullish(),
      score: z.number().nullish(),
      summary: z.record(z.string(), z.unknown()).nullish(),
    });

export const ExecutionReportView = z.object({
      executionId: z.string(),
      importReportArtifactId: z.string().nullish(),
      reportArtifactId: z.string().nullish(),
    });

export const ExecutionSlot = z.object({
      executionId: z.string().nullish(),
      importReportArtifactId: z.lazy(() => ArtifactSlot).nullish(),
      reportArtifactId: z.lazy(() => ArtifactSlot).nullish(),
      status: z.string().nullish(),
    });

export const ExecutionView = z.object({
      applied: z.boolean().nullish(),
      executionId: z.string().nullish(),
      importReportArtifactId: z.string().nullish(),
      issues: z.array(z.lazy(() => IssueView)).optional(),
      payloadHash: z.string().nullish(),
      reportArtifactId: z.string().nullish(),
      status: z.string(),
      steps: z.array(z.record(z.string(), z.unknown())).optional(),
    });

export const ExemptionCreateRequest = z.object({
      environmentKey: z.string().optional(),
      expiresAt: z.string().nullish(),
      reason: z.string(),
      waiver: z.string().optional(),
    });

export const ExemptionList = z.object({
      items: z.array(z.lazy(() => ExemptionView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ExemptionView = z.object({
      environmentKey: z.string().optional(),
      expiresAt: z.string().nullish(),
      grantedBy: z.string().optional(),
      id: z.string(),
      projectId: z.string().nullish(),
      reason: z.string().optional(),
      revoked: z.boolean().optional(),
      waiver: z.string().optional(),
    });

export const ExpiringCredentialList = z.object({
      items: z.array(z.lazy(() => ExpiringCredentialView)).optional(),
    });

export const ExpiringCredentialView = z.object({
      daysRemaining: z.number().int().nullish(),
      environmentKey: z.string(),
      expiresAt: z.string().nullish(),
      scope: z.string().nullish(),
      secretRef: z.string(),
    });

export const ExplainRequest = z.object({
      projectId: z.string(),
    });

export const ExplainResultView = z.object({
      explanationArtifactId: z.string().nullish(),
    });

export const FreshnessSlot = z.object({
      artifactId: z.string().nullish(),
      probedAt: z.string().nullish(),
      state: z.string().nullish(),
    });

export const FrontendPackageFileView = z.object({
      contentHash: z.string().optional(),
      kind: z.string().optional(),
      path: z.string(),
      sizeBytes: z.number().int().optional(),
    });

export const FrontendPackageFilesView = z.object({
      artifactId: z.string().optional(),
      blueprintVersion: z.string().optional(),
      files: z.array(z.lazy(() => FrontendPackageFileView)).optional(),
      projectId: z.string().optional(),
    });

export const GateDecisionView = z.object({
      affectedStages: z.array(z.string()).optional(),
      approvals: z.array(z.lazy(() => ApprovalDecision)).optional(),
      blueprintVersion: z.string().nullish(),
      changeLevel: z.string().nullish(),
      gateId: z.string(),
      invalidatedReason: z.string().nullish(),
      invalidation: z.lazy(() => InvalidationPlanView).nullish(),
      nextStage: z.string().nullish(),
      requiredApprovals: z.number().int().optional(),
      status: z.string(),
    });

export const GateExplanationView = z.object({
      afterApproval: z.record(z.string(), z.unknown()).optional(),
      canChange: z.array(z.record(z.string(), z.unknown())).optional(),
      found: z.lazy(() => GateFoundView).optional(),
      gateId: z.string(),
      gateType: z.string(),
      uncertain: z.lazy(() => GateUncertainView).optional(),
      why: z.record(z.string(), z.array(z.lazy(() => DecisionView))).optional(),
      willDo: z.lazy(() => GateWillDoView).optional(),
    });

export const GateFoundView = z.object({
      boundObjects: z.array(z.lazy(() => BoundObjectView)).optional(),
    });

export const GateList = z.object({
      items: z.array(z.lazy(() => GateView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const GateRejectionView = z.object({
      gateId: z.string(),
      stageId: z.string().nullish(),
      status: z.string(),
    });

export const GateSummaryList = z.object({
      items: z.array(z.lazy(() => GateSummaryView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const GateSummaryView = z.object({
      approvalCount: z.number().int().optional(),
      createdAt: z.string().nullish(),
      deploymentRunId: z.string().nullish(),
      environmentKey: z.string().nullish(),
      highOpenQuestionCount: z.number().int().optional(),
      id: z.string(),
      projectId: z.string().nullish(),
      projectName: z.string().nullish(),
      requiredApprovals: z.number().int().optional(),
      stageId: z.string().nullish(),
      status: z.string(),
      type: z.string(),
      unresolvedWarningCount: z.number().int().optional(),
      warningCount: z.number().int().optional(),
      workflowId: z.string().nullish(),
    });

export const GateUncertainView = z.object({
      openQuestions: z.array(z.record(z.string(), z.unknown())).optional(),
      warnings: z.array(z.lazy(() => GateWarningView)).optional(),
    });

export const GateView = z.object({
      approvals: z.array(z.lazy(() => ApprovalView)).optional(),
      blueprintRef: z.record(z.string(), z.unknown()).nullish(),
      boundObjects: z.array(z.lazy(() => BoundObjectView)).optional(),
      confirmationItems: z.array(z.lazy(() => ConfirmationItemView)).optional(),
      createdAt: z.string().nullish(),
      deploymentRunId: z.string().nullish(),
      environmentKey: z.string().nullish(),
      id: z.string(),
      invalidatedReason: z.string().nullish(),
      requiredApprovals: z.number().int().optional(),
      riskSummaryRef: z.record(z.string(), z.unknown()).nullish(),
      stageId: z.string().nullish(),
      status: z.string(),
      type: z.string(),
      warnings: z.array(z.lazy(() => GateWarningView)).optional(),
      workflowId: z.string().nullish(),
    });

export const GateWarningView = z.object({
      businessKey: z.string().nullish(),
      issueId: z.string().nullish(),
      level: z.string().optional(),
      message: z.string().optional(),
      remediation: z.string().nullish(),
      ruleId: z.string().nullish(),
      sourceStage: z.string().nullish(),
    });

export const GateWillDoView = z.object({
      confirmationItems: z.array(z.lazy(() => ConfirmationItemView)).optional(),
      environmentKey: z.string().nullish(),
      gateType: z.string().nullish(),
      riskSummaryRef: z.record(z.string(), z.unknown()).nullish(),
    });

export const GrantCreateRequest = z.object({
      classification: z.string(),
      expiresAt: z.string().nullish(),
      reason: z.string().optional(),
      userId: z.string(),
    });

export const GrantList = z.object({
      items: z.array(z.lazy(() => GrantView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const GrantView = z.object({
      classification: z.string(),
      expiresAt: z.string().nullish(),
      grantedBy: z.string().optional(),
      id: z.string().nullish(),
      projectId: z.string().nullish(),
      reason: z.string().optional(),
      revoked: z.boolean().optional(),
      subject: z.string().optional(),
      userId: z.string(),
    });

export const GraphEdgeView = z.object({
      edgeKind: z.string().optional(),
      field: z.string().optional(),
      fromBusinessKey: z.string().optional(),
      fromDhis2Type: z.string().nullish(),
      toBusinessKey: z.string().optional(),
      toDhis2Type: z.string().nullish(),
    });

export const GraphFiltersView = z.object({
      businessKeys: z.array(z.string()).optional(),
      dhis2Types: z.array(z.string()).optional(),
    });

export const GraphNodeView = z.object({
      businessKey: z.string(),
      dhis2Type: z.string().nullish(),
      disposition: z.string().nullish(),
      inDegree: z.number().int().optional(),
      inPayload: z.boolean().optional(),
      managed: z.boolean().optional(),
      outDegree: z.number().int().optional(),
      uid: z.string().nullish(),
    });

export const ImpactView = z.object({
      affectedArtifactTypes: z.array(z.string()).optional(),
      affectedObjectKeys: z.array(z.string()).optional(),
      affectedPages: z.array(z.string()).optional(),
      affectedStages: z.array(z.string()).optional(),
      changeRequestId: z.string().nullish(),
      deletionCandidates: z.array(z.string()).optional(),
      impactReportArtifactId: z.string().nullish(),
      invalidation: z.record(z.string(), z.unknown()).optional(),
    });

export const ImportProjectBody = z.object({
      artifactId: z.string(),
    });

export const InputCompletenessView = z.object({
      missing: z.array(z.lazy(() => MissingCheckView)).optional(),
      ratio: z.number().nullish(),
      satisfied: z.number().int().optional(),
      total: z.number().int().optional(),
    });

export const InputPage = z.object({
      items: z.array(z.lazy(() => InputView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const InputView = z.object({
      artifactId: z.string(),
      contentHash: z.string().nullish(),
      createdAt: z.string().nullish(),
      dataClassification: z.string().nullish(),
      errorCode: z.string().nullish(),
      errorMessage: z.string().nullish(),
      filename: z.string().nullish(),
      mediaType: z.string().nullish(),
      nrrArtifactId: z.string().nullish(),
      parseId: z.string().nullish(),
      parseRunId: z.string().nullish(),
      parseStatus: z.string().nullish(),
      parserType: z.string().nullish(),
      parserVersion: z.string().nullish(),
      sizeBytes: z.number().int().optional(),
      usedModel: z.boolean().nullish(),
    });

export const InvalidationPlanView = z.object({
      cancelRuns: z.array(z.string()).optional(),
      invalidateGates: z.array(z.string()).optional(),
      invalidateStages: z.array(z.string()).optional(),
      outdatedRuns: z.array(z.string()).optional(),
      reason: z.string().nullish(),
    });

export const IssueView = z.object({
      artifactId: z.string().nullish(),
      businessKey: z.string().nullish(),
      description: z.string().optional(),
      details: z.record(z.string(), z.unknown()).optional(),
      dhis2Type: z.string().nullish(),
      issueId: z.string(),
      level: z.string().nullish(),
      location: z.string().nullish(),
      objectKey: z.string().nullish(),
      objectUid: z.string().nullish(),
      remediation: z.string().nullish(),
      ruleId: z.string().nullish(),
      section: z.string().nullish(),
      sourceStage: z.string().nullish(),
      suggestedAction: z.string().nullish(),
    });

export const LineageView = z.object({
      artifactId: z.string(),
      derived: z.array(z.string()).optional(),
      sources: z.array(z.string()).optional(),
    });

export const LlmExchangeDetailView = z.object({
      completedAt: z.string().nullish(),
      createdAt: z.string().nullish(),
      errorCode: z.string().nullish(),
      errorMessage: z.string().nullish(),
      exchangeId: z.string(),
      expiresAt: z.string().nullish(),
      inputArtifactId: z.string(),
      inputTokens: z.number().int().optional(),
      latencyMs: z.number().int().nullish(),
      model: z.string(),
      outputTokens: z.number().int().optional(),
      provider: z.string(),
      providerClass: z.string().nullish(),
      request: z.lazy(() => LlmExchangeRequestView),
      requestId: z.string().nullish(),
      response: z.lazy(() => LlmExchangeResponseView).nullish(),
      sequence: z.number().int(),
      status: z.string(),
    });

export const LlmExchangeImageView = z.object({
      contentHash: z.string(),
      label: z.string(),
      mediaType: z.string(),
      sizeBytes: z.number().int(),
    });

export const LlmExchangeMessageView = z.object({
      content: z.string(),
      images: z.array(z.lazy(() => LlmExchangeImageView)).optional(),
      role: z.string(),
    });

export const LlmExchangeRequestView = z.object({
      agentHint: z.record(z.string(), z.unknown()).nullish(),
      jsonSchema: z.record(z.string(), z.unknown()).nullish(),
      maxOutputTokens: z.number().int().nullish(),
      messages: z.array(z.lazy(() => LlmExchangeMessageView)).optional(),
      schemaName: z.string().nullish(),
      temperature: z.number().nullish(),
    });

export const LlmExchangeResponseView = z.object({
      finishReason: z.string().nullish(),
      model: z.string().nullish(),
      payload: z.record(z.string(), z.unknown()).nullish(),
      requestId: z.string().nullish(),
      storageTruncated: z.boolean().optional(),
      text: z.string().nullish(),
      truncated: z.boolean().optional(),
      usage: z.record(z.string(), z.number().int()).nullish(),
    });

export const LlmExchangeSummaryView = z.object({
      completedAt: z.string().nullish(),
      createdAt: z.string().nullish(),
      errorCode: z.string().nullish(),
      errorMessage: z.string().nullish(),
      exchangeId: z.string(),
      inputTokens: z.number().int().optional(),
      latencyMs: z.number().int().nullish(),
      model: z.string(),
      outputTokens: z.number().int().optional(),
      provider: z.string(),
      providerClass: z.string().nullish(),
      requestId: z.string().nullish(),
      sequence: z.number().int(),
      status: z.string(),
    });

export const LlmLogView = z.object({
      items: z.array(z.lazy(() => LlmExchangeSummaryView)).optional(),
      limit: z.number().int().optional(),
      offset: z.number().int().optional(),
      parseRunId: z.string().nullish(),
      parseStatus: z.string().nullish(),
      total: z.number().int().optional(),
    });

export const LoginRequest = z.object({
      password: z.string(),
      subject: z.string(),
    });

export const ManagedObjectView = z.object({
      businessKey: z.string(),
      code: z.string().nullish(),
      controlledFields: z.array(z.string()).optional(),
      dhis2Type: z.string().nullish(),
      disposition: z.string().nullish(),
      observedHash: z.string().nullish(),
      observedLastUpdated: z.string().nullish(),
      uid: z.string().nullish(),
    });

export const ManagedScopePage = z.object({
      items: z.array(z.lazy(() => ManagedObjectView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ManualCheckBody = z.object({
      note: z.string().optional(),
      outcome: z.string(),
    });

export const ManualCheckView = z.object({
      executionId: z.string().nullish(),
      note: z.string().nullish(),
      releasesLock: z.boolean().nullish(),
      resolved: z.boolean().nullish(),
      status: z.string().nullish(),
    });

export const MarkReadView = z.object({
      id: z.string(),
      message: z.string().nullish(),
      objectRef: z.record(z.string(), z.unknown()).optional(),
      projectId: z.string().nullish(),
      read: z.boolean(),
      type: z.string().nullish(),
    });

export const MemberList = z.object({
      items: z.array(z.lazy(() => MemberView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const MemberPutRequest = z.object({
      members: z.array(z.lazy(() => MemberView)).optional(),
    });

export const MemberView = z.object({
      id: z.string().nullish(),
      projectId: z.string().nullish(),
      role: z.string().nullish(),
      subject: z.string().optional(),
      userId: z.string(),
    });

export const MissingCheckView = z.object({
      key: z.string(),
      label: z.string(),
    });

export const ModelCapabilities = z.object({
      maxContextTokens: z.number().int().nullish(),
      structuredOutput: z.boolean().optional(),
      vision: z.boolean().optional(),
    });

export const ModelList = z.object({
      items: z.array(z.lazy(() => ModelView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ModelView = z.object({
      apiKeySecretRef: z.string().nullish(),
      applicableAgents: z.record(z.string(), z.union([z.number().int(), z.string()])).optional(),
      capabilities: z.lazy(() => ModelCapabilities).optional(),
      enabled: z.boolean().optional(),
      endpoint: z.string().nullish(),
      id: z.string().nullish(),
      lastEvalRunId: z.string().nullish(),
      lastEvalScore: z.number().nullish(),
      model: z.string().nullish(),
      provider: z.string().nullish(),
      providerClass: z.string().nullish(),
      ref: z.string().nullish(),
      unitPriceInputPer1k: z.number().optional(),
      unitPriceOutputPer1k: z.number().optional(),
    });

export const NotificationChannels = z.object({
      secretRef: z.string().nullish(),
      target: z.string().nullish(),
      type: z.string().nullish(),
    });

export const NotificationPage = z.object({
      items: z.array(z.lazy(() => NotificationView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const NotificationView = z.object({
      createdAt: z.string().nullish(),
      id: z.string(),
      message: z.string().nullish(),
      objectRef: z.record(z.string(), z.unknown()).optional(),
      projectId: z.string().nullish(),
      read: z.boolean().optional(),
      type: z.string().nullish(),
    });

export const NrrItemView = z.object({
      actors: z.array(z.string()).optional(),
      artifactId: z.string().nullish(),
      businessKey: z.string().nullish(),
      category: z.string().nullish(),
      confidence: z.number().nullish(),
      openQuestions: z.array(z.string()).optional(),
      priority: z.string().nullish(),
      rationale: z.string().nullish(),
      source: z.record(z.string(), z.unknown()).optional(),
      statement: z.string().nullish(),
      status: z.string().nullish(),
    });

export const NrrPage = z.object({
      items: z.array(z.lazy(() => NrrItemView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ObjectDeletionRequest = z.object({
      deletionArtifactId: z.string(),
      expectedHash: z.string(),
      projectId: z.string(),
    });

export const ObjectDeletionView = z.object({
      applied: z.boolean().nullish(),
      deletionArtifactId: z.string(),
      detail: z.string().nullish(),
      environmentKey: z.string(),
      executionId: z.string().nullish(),
      importReportArtifactId: z.string().nullish(),
      issues: z.array(z.lazy(() => IssueView)).optional(),
      payloadHash: z.string().nullish(),
      reportArtifactId: z.string().nullish(),
      status: z.string().optional(),
      steps: z.array(z.record(z.string(), z.unknown())).optional(),
    });

export const ObservationBaselineView = z.object({
      discoveryResultId: z.string().nullish(),
      observedHash: z.string().nullish(),
      observedLastUpdated: z.string().nullish(),
      sourceEnvironment: z.string().nullish(),
    });

export const OidcExchangeRequest = z.object({
      code: z.string(),
      redirectUri: z.string(),
    });

export const OidcRequestView = z.object({
      authorizationEndpoint: z.string(),
      clientId: z.string(),
      nonce: z.string().optional(),
      redirectUri: z.string(),
      responseType: z.string().optional(),
      scope: z.string().optional(),
      state: z.string().optional(),
    });

export const PackageFileView = z.object({
      contentHash: z.string().nullish(),
      dhis2Type: z.string().nullish(),
      objectCount: z.number().int().nullish(),
      path: z.string(),
      role: z.string().optional(),
    });

export const PackageFilesView = z.object({
      buildId: z.string().nullish(),
      environmentKey: z.string().nullish(),
      files: z.array(z.lazy(() => PackageFileView)).optional(),
      kind: z.string().nullish(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      packageHash: z.string().nullish(),
      projectId: z.string().nullish(),
      total: z.number().int().nullish(),
      version: z.string().nullish(),
    });

export const ParseInputRequest = z.object({
      options: z.lazy(() => ParseOptions).nullish(),
      stageId: z.string().nullish(),
    });

export const ParseOptions = z.object({
      columnMapping: z.record(z.string(), z.string()).optional(),
      delimiter: z.string().nullish(),
      exportedAs: z.string().nullish(),
      hasHeader: z.boolean().nullish(),
      parser: z.string().nullish(),
      sheet: z.string().nullish(),
    });

export const PasswordSetRequest = z.object({
      mustChangePassword: z.boolean().optional(),
      password: z.string(),
    });

export const PatchModelRequest = z.object({
      apiKeySecretRef: z.string().nullish(),
      applicableAgents: z.record(z.string(), z.union([z.number().int(), z.string()])).nullish(),
      capabilities: z.record(z.string(), z.unknown()).nullish(),
      enabled: z.boolean().nullish(),
      endpoint: z.string().nullish(),
      unitPriceInputPer1k: z.number().nullish(),
      unitPriceOutputPer1k: z.number().nullish(),
    });

export const PatchProviderRequest = z.object({
      adapter: z.string().nullish(),
      apiKeySecretRef: z.string().nullish(),
      enabled: z.boolean().nullish(),
      endpoint: z.string().nullish(),
      providerClass: z.string().nullish(),
      suggestedModels: z.array(z.string()).max(100).nullish(),
      vendor: z.string().nullish(),
    });

export const PatchSubmitView = z.object({
      assignedUids: z.record(z.string(), z.string()).optional(),
      draftHash: z.string().nullish(),
      gateInvalidated: z.boolean().nullish(),
      issues: z.array(z.lazy(() => IssueView)).optional(),
      patchStatus: z.string().nullish(),
      questions: z.array(z.string()).optional(),
      stageId: z.string(),
    });

export const PayloadSlot = z.object({
      hash: z.string().nullish(),
      packageArtifactId: z.string().nullish(),
    });

export const PolicyView = z.object({
      ceiling: z.lazy(() => EffectivePolicyView).optional(),
      effective: z.lazy(() => EffectivePolicyView).optional(),
      isDefault: z.boolean().optional(),
    });

export const ProbeResultView = z.object({
      capabilityProfileArtifactId: z.string().nullish(),
      profileVersion: z.string().nullish(),
      version: z.string().nullish(),
    });

export const ProjectPage = z.object({
      items: z.array(z.lazy(() => ProjectView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ProjectPolicyView = z.object({
      bindingEnvironment: z.string().nullish(),
      ceiling: z.lazy(() => EffectivePolicyView).optional(),
      effective: z.lazy(() => EffectivePolicyView).optional(),
      environmentPolicies: z.record(z.string(), z.string()).optional(),
      isDefault: z.boolean().optional(),
      projectId: z.string().nullish(),
    });

export const ProjectSettingsBody = z.object({
      gateReminderHours: z.number().int().nullish(),
      notificationEmails: z.array(z.string()).nullish(),
      notificationWebhookRef: z.string().nullish(),
    });

export const ProjectSettingsView = z.object({
      gateReminderHours: z.number().int().nullish(),
      notificationEmails: z.array(z.string()).optional(),
      notificationWebhookRef: z.string().nullish(),
    });

export const ProjectStageView = z.object({
      attempt: z.number().int().optional(),
      deploymentRunId: z.string().nullish(),
      id: z.string(),
      stageType: z.string(),
      status: z.string(),
    });

export const ProjectView = z.object({
      archived: z.boolean().optional(),
      createdAt: z.string().nullish(),
      currentBlueprintVersion: z.string().nullish(),
      currentStageId: z.string().nullish(),
      defaultLanguage: z.string().nullish(),
      deliveryMode: z.string().optional(),
      description: z.string().nullish(),
      id: z.string(),
      milestoneStatus: z.string().nullish(),
      name: z.string(),
      ownerName: z.string().nullish(),
      projectType: z.string().nullish(),
      stages: z.array(z.lazy(() => ProjectStageView)).optional(),
      targetEnvironments: z.array(z.lazy(() => TargetEnvironmentView)).optional(),
      translationLanguages: z.array(z.string()).optional(),
      updatedAt: z.string().nullish(),
      versionConstraints: z.string().nullish(),
      workflowId: z.string().nullish(),
      workflowStatus: z.string().nullish(),
    });

export const PromptDetailView = z.object({
      agentType: z.string().nullish(),
      applicableStages: z.array(z.string()).optional(),
      blueprintSchemaRange: z.string().nullish(),
      body: z.string().nullish(),
      dhis2VersionRange: z.string().nullish(),
      id: z.string().nullish(),
      inputSchemaRef: z.string().nullish(),
      name: z.string().nullish(),
      outputSchemaRef: z.string().nullish(),
      prohibitions: z.array(z.record(z.string(), z.unknown())).optional(),
      sourceHash: z.string().nullish(),
      systemRules: z.string().nullish(),
      version: z.number().int().nullish(),
    });

export const PromptList = z.object({
      items: z.array(z.lazy(() => PromptListView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const PromptListView = z.object({
      active: z.boolean().optional(),
      agentType: z.string().nullish(),
      blueprintSchemaRange: z.string().nullish(),
      dhis2VersionRange: z.string().nullish(),
      id: z.string().nullish(),
      name: z.string().nullish(),
      prohibitions: z.array(z.record(z.string(), z.unknown())).optional(),
      qualifiedName: z.string().nullish(),
      sourceHash: z.string().nullish(),
      version: z.number().int().nullish(),
    });

export const ProtectionSpec = z.object({
      commitWindows: z.array(z.lazy(() => CommitWindowSpec)).optional(),
      deletionPolicy: z.string().nullish(),
      emergencyLock: z.boolean().optional(),
      operatorRole: z.string().nullish(),
      requiredApprovals: z.number().int().nullish(),
      timezone: z.string().optional(),
    });

export const ProviderCatalogueEntry = z.object({
      displayName: z.string(),
      docsUrl: z.string().optional(),
      endpoint: z.string(),
      key: z.string(),
      keyRefSuggestion: z.string().optional(),
      local: z.boolean().optional(),
      models: z.array(z.string()).optional(),
      note: z.string().optional(),
      wireProtocol: z.string().optional(),
    });

export const ProviderCatalogueView = z.object({
      items: z.array(z.lazy(() => ProviderCatalogueEntry)).optional(),
      total: z.number().int().optional(),
    });

export const ProviderHealthView = z.object({
      detail: z.string().nullish(),
      healthy: z.boolean().optional(),
      ref: z.string().nullish(),
    });

export const ProviderList = z.object({
      items: z.array(z.lazy(() => ProviderView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ProviderView = z.object({
      adapter: z.string().nullish(),
      apiKeySecretRef: z.string().nullish(),
      enabled: z.boolean().optional(),
      endpoint: z.string().nullish(),
      id: z.string().nullish(),
      name: z.string().nullish(),
      providerClass: z.string().nullish(),
      suggestedModels: z.array(z.string()).optional(),
      vendor: z.string().optional(),
    });

export const QuestionAnswerView = z.object({
      answeredAt: z.string().nullish(),
      customText: z.string().nullish(),
      suggestionId: z.string().nullish(),
      suggestionIds: z.array(z.string()).optional(),
      suggestionText: z.string().nullish(),
      suggestionTexts: z.array(z.string()).optional(),
      text: z.string().nullish(),
    });

export const QuestionCreatedView = z.object({
      questionId: z.string().nullish(),
    });

export const QuestionList = z.object({
      items: z.array(z.lazy(() => QuestionView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const QuestionPage = z.object({
      items: z.array(z.lazy(() => QuestionView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const QuestionSourceView = z.object({
      agentRunId: z.string().nullish(),
      kind: z.string().nullish(),
      localId: z.string().nullish(),
    });

export const QuestionView = z.object({
      affects: z.array(z.string()).optional(),
      answer: z.lazy(() => QuestionAnswerView).nullish(),
      answerMode: z.string().optional(),
      answeredAt: z.string().nullish(),
      answeredBy: z.string().nullish(),
      blocking: z.boolean().optional(),
      createdAt: z.string().nullish(),
      id: z.string(),
      projectId: z.string().nullish(),
      severity: z.string().optional(),
      source: z.lazy(() => QuestionSourceView).optional(),
      stageId: z.string().nullish(),
      status: z.string().optional(),
      suggestions: z.array(z.lazy(() => SuggestionView)).optional(),
      text: z.string().optional(),
      workflowId: z.string().nullish(),
    });

export const QuestionnaireAnswerItem = z.object({
      customText: z.string().nullish(),
      questionId: z.string(),
      suggestionIds: z.array(z.string()).optional(),
    });

export const QuestionnaireAnswerResult = z.object({
      questionId: z.string(),
      status: z.string(),
    });

export const QuestionnaireAnswerView = z.object({
      answers: z.array(z.lazy(() => QuestionnaireAnswerResult)).optional(),
      questions: z.array(z.lazy(() => QuestionView)).optional(),
      resumed: z.boolean().optional(),
      runId: z.string().nullish(),
      status: z.string().nullish(),
    });

export const ReconcileView = z.object({
      evidence: z.record(z.string(), z.unknown()).optional(),
      outcome: z.string(),
    });

export const RecordRegressionRequest = z.object({
      model: z.string().nullish(),
      note: z.string().nullish(),
      passed: z.boolean().optional(),
      promptName: z.string(),
      promptVersion: z.number().int(),
      recordedBy: z.string().nullish(),
      source: z.string().nullish(),
    });

export const RegisterModelRequest = z.object({
      apiKeySecretRef: z.string().nullish(),
      applicableAgents: z.record(z.string(), z.union([z.number().int(), z.string()])).optional(),
      capabilities: z.record(z.string(), z.unknown()).optional(),
      enabled: z.boolean().optional(),
      endpoint: z.string().nullish(),
      model: z.string(),
      provider: z.string(),
      unitPriceInputPer1k: z.number().optional(),
      unitPriceOutputPer1k: z.number().optional(),
    });

export const RegisterProviderRequest = z.object({
      adapter: z.string().optional(),
      apiKeySecretRef: z.string().optional(),
      enabled: z.boolean().optional(),
      endpoint: z.string(),
      name: z.string(),
      providerClass: z.enum(["LOCAL_ONLY", "PRIVATE_NETWORK", "APPROVED_EXTERNAL", "UNRESTRICTED"] as const),
      suggestedModels: z.array(z.string()).max(100).optional(),
      vendor: z.string().optional(),
    });

export const RegistryEntryView = z.object({
      businessKey: z.string(),
      code: z.string().nullish(),
      controlledFields: z.array(z.string()).optional(),
      decisionId: z.string().nullish(),
      dhis2Type: z.string().nullish(),
      discoveryResultId: z.string().nullish(),
      disposition: z.string().nullish(),
      environmentBindings: z.record(z.string(), z.string()).optional(),
      managed: z.boolean().optional(),
      observed: z.lazy(() => ObservationBaselineView).optional(),
      observedHash: z.string().nullish(),
      observedLastUpdated: z.string().nullish(),
      sourceEnvironment: z.string().nullish(),
      status: z.string().nullish(),
      uid: z.string().nullish(),
    });

export const RegistryPage = z.object({
      items: z.array(z.lazy(() => RegistryEntryView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const RegressionList = z.object({
      items: z.array(z.lazy(() => RegressionRecordView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const RegressionRecordView = z.object({
      id: z.string().nullish(),
      passed: z.boolean().optional(),
      promptVersionId: z.string().nullish(),
    });

export const RejectChangeRequestBody = z.object({
      reason: z.string().optional(),
    });

export const RejectGateBody = z.object({
      gateType: z.string().optional(),
      reason: z.string(),
      warningDispositions: z.array(z.lazy(() => WarningDisposition)).optional(),
    });

export const RelaxPolicyRequest = z.object({
      change: z.record(z.string(), z.unknown()),
      reason: z.string(),
      scope: z.string().nullish(),
    });

export const RelaxationChangeView = z.object({
      addClasses: z.array(z.string()).optional(),
      level: z.string(),
    });

export const RelaxationView = z.object({
      change: z.lazy(() => RelaxationChangeView).nullish(),
      createdAt: z.string().nullish(),
      id: z.string().nullish(),
      operator: z.string().nullish(),
      reason: z.string().nullish(),
      roleChecked: z.boolean().nullish(),
      roleGate: z.string().nullish(),
      scope: z.string().nullish(),
    });

export const ReproduceView = z.object({
      hash: z.string().nullish(),
      matches: z.boolean().nullish(),
    });

export const RoleAssignmentRequest = z.object({
      projectId: z.string().nullish(),
      role: z.string(),
    });

export const RoleAssignmentView = z.object({
      projectId: z.string().nullish(),
      revoked: z.boolean().nullish(),
      role: z.string(),
      userId: z.string(),
    });

export const RoleList = z.object({
      items: z.array(z.lazy(() => RoleView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const RoleView = z.object({
      actions: z.array(z.string()).optional(),
      name: z.string(),
    });

export const RunDetailView = z.object({
      gates: z.array(z.lazy(() => GateView)).optional(),
      run: z.lazy(() => RunView),
      stages: z.array(z.lazy(() => RunStages)).optional(),
    });

export const RunEvaluationRequest = z.object({
      agentType: z.string(),
      evalSetVersion: z.string().nullish(),
      model: z.string(),
      qualifiedPrompt: z.string(),
    });

export const RunList = z.object({
      items: z.array(z.lazy(() => RunView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const RunPage = z.object({
      items: z.array(z.lazy(() => RunView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const RunStages = z.object({
      attempt: z.number().int().optional(),
      id: z.string(),
      stageType: z.string(),
      status: z.string(),
    });

export const RunSummaryView = z.object({
      approval: z.lazy(() => ApprovalSlot).optional(),
      blueprintVersion: z.string().nullish(),
      capabilityFreshness: z.lazy(() => FreshnessSlot).nullish(),
      changeStats: z.lazy(() => ChangeStatsView).optional(),
      commitWindow: z.lazy(() => CommitWindowSlot).optional(),
      connection: z.lazy(() => ConnectionSlot).nullish(),
      dhis2Version: z.string().nullish(),
      diffReportArtifactId: z.string().nullish(),
      drift: z.lazy(() => DriftSlot).nullish(),
      environmentClass: z.string().nullish(),
      environmentKey: z.string().nullish(),
      evaluatedAt: z.string().nullish(),
      execution: z.lazy(() => ExecutionSlot).optional(),
      gates: z.array(z.lazy(() => GateView)).optional(),
      maintenanceActions: z.array(z.record(z.string(), z.unknown())).optional(),
      outdated: z.boolean().optional(),
      payload: z.lazy(() => PayloadSlot).optional(),
      profileVersion: z.string().nullish(),
      projectId: z.string().nullish(),
      projectName: z.string().nullish(),
      runId: z.string(),
      snapshot: z.lazy(() => ArtifactSlot).nullish(),
      stages: z.array(z.lazy(() => RunStages)).optional(),
      status: z.string(),
      validation: z.lazy(() => ValidationSlot).optional(),
      verification: z.lazy(() => VerificationSlot).optional(),
      workflowId: z.string().nullish(),
    });

export const RunView = z.object({
      blueprintVersion: z.string().nullish(),
      commitOperator: z.string().nullish(),
      completedAt: z.string().nullish(),
      environmentClass: z.string().nullish(),
      environmentKey: z.string().nullish(),
      executionId: z.string().nullish(),
      id: z.string(),
      lastVerifiedAt: z.string().nullish(),
      outdated: z.boolean().optional(),
      postVerifyArtifactId: z.string().nullish(),
      projectId: z.string().nullish(),
      projectName: z.string().nullish(),
      startedAt: z.string().nullish(),
      status: z.string(),
      validateOperator: z.string().nullish(),
      validatePayloadHash: z.string().nullish(),
      workflowId: z.string().nullish(),
    });

export const SecretDeletedView = z.object({
      deleted: z.boolean().optional(),
      name: z.string(),
    });

export const SecretListView = z.object({
      generatedAt: z.string().nullish(),
      items: z.array(z.lazy(() => SecretView)).optional(),
      total: z.number().int().optional(),
    });

export const SecretView = z.object({
      algo: z.string(),
      createdAt: z.string().nullish(),
      createdBy: z.string().optional(),
      name: z.string(),
      ref: z.string(),
      rotatedAt: z.string().nullish(),
      updatedAt: z.string().nullish(),
    });

export const SecretWriteRequest = z.object({
      material: z.string(),
    });

export const SectionLayoutView = z.object({
      entryContainer: z.string().nullish(),
      keyField: z.string().nullish(),
      section: z.string(),
    });

export const SectionSchemaIndexView = z.object({
      availableVersions: z.array(z.number().int()).optional(),
      currentSchemaVersion: z.number().int().nullish(),
      sections: z.array(z.lazy(() => SectionLayoutView)).optional(),
    });

export const SectionSchemaView = z.object({
      $id: z.string().nullish(),
      $schema: z.string().nullish(),
      definitions: z.record(z.string(), z.unknown()).nullish(),
      title: z.string().nullish(),
      type: z.string().nullish(),
    });

export const SessionView = z.object({
      authMode: z.string().optional(),
      authenticated: z.boolean().optional(),
      csrfToken: z.string().nullish(),
      displayName: z.string().nullish(),
      expiresAt: z.string().nullish(),
      mustChangePassword: z.boolean().optional(),
      roles: z.array(z.string()).optional(),
      subject: z.string().nullish(),
      userId: z.string().nullish(),
    });

export const SettingsUpdateView = z.object({
      updated: z.array(z.string()).optional(),
    });

export const SetupConfigurationView = z.object({
      authMode: z.string().nullish(),
      httpsOnly: z.boolean().optional(),
      migrationsMode: z.string().nullish(),
      problems: z.array(z.string()).optional(),
      providerMode: z.string().nullish(),
      workflowEngine: z.string().nullish(),
    });

export const SetupCredentialsView = z.object({
      masterKeyConfigured: z.boolean().optional(),
      named: z.number().int().optional(),
      scheme: z.string().optional(),
      storeAvailable: z.boolean().optional(),
      unresolvable: z.array(z.lazy(() => SetupRefHolderView)).optional(),
    });

export const SetupEngineView = z.object({
      detail: z.record(z.string(), z.unknown()).optional(),
      durable: z.boolean().optional(),
      name: z.string().nullish(),
    });

export const SetupEnvironmentItem = z.object({
      environmentClass: z.string().nullish(),
      environmentKey: z.string(),
      lastTestedAt: z.union([z.string(), z.string()]).nullish(),
      status: z.string(),
    });

export const SetupEnvironmentsView = z.object({
      inService: z.number().int().optional(),
      items: z.array(z.lazy(() => SetupEnvironmentItem)).optional(),
      neverTested: z.array(z.string()).optional(),
      nonProductionInService: z.number().int().optional(),
      total: z.number().int().optional(),
    });

export const SetupIdentityView = z.object({
      authMode: z.string().nullish(),
      bootstrapAdminDeclared: z.boolean().optional(),
      enforced: z.boolean().optional(),
      m4Enabled: z.boolean().optional(),
      pendingPasswordChange: z.boolean().optional(),
      users: z.number().int().optional(),
    });

export const SetupIntelligenceView = z.object({
      models: z.number().int().optional(),
      modelsEnabled: z.number().int().optional(),
      providers: z.number().int().optional(),
      providersEnabled: z.number().int().optional(),
    });

export const SetupProjectsView = z.object({
      active: z.number().int().optional(),
      total: z.number().int().optional(),
    });

export const SetupRefHolderView = z.object({
      holders: z.array(z.string()).optional(),
      reason: z.string(),
      ref: z.string(),
    });

export const SetupStartupView = z.object({
      appliedSchemas: z.array(z.string()).optional(),
      authMode: z.string().nullish(),
      migrationsMode: z.string().nullish(),
      providersInitialized: z.array(z.string()).optional(),
      providersMissing: z.array(z.string()).optional(),
      registered: z.record(z.string(), z.number().int()).optional(),
      skippedSchemas: z.array(z.string()).optional(),
    });

export const SetupView = z.object({
      configuration: z.lazy(() => SetupConfigurationView),
      credentials: z.lazy(() => SetupCredentialsView).nullish(),
      engine: z.lazy(() => SetupEngineView).nullish(),
      environments: z.lazy(() => SetupEnvironmentsView),
      generatedAt: z.union([z.string(), z.string()]).nullish(),
      identity: z.lazy(() => SetupIdentityView),
      intelligence: z.lazy(() => SetupIntelligenceView).nullish(),
      projects: z.lazy(() => SetupProjectsView),
      providers: z.record(z.string(), z.string()).optional(),
      startup: z.lazy(() => SetupStartupView).nullish(),
    });

export const StageContextView = z.object({
      activeDraftId: z.string().nullish(),
      deliveryMode: z.string().nullish(),
      projectId: z.string(),
      projectName: z.string().optional(),
      stage: z.string(),
      stageStatus: z.string(),
      targetEnvironments: z.array(z.string()).optional(),
      workflowId: z.string(),
    });

export const StageDetailView = z.object({
      activeDraftId: z.string().nullish(),
      artifactCount: z.number().int().optional(),
      attempt: z.number().int().optional(),
      blockingIssueCount: z.number().int().optional(),
      completedAt: z.string().nullish(),
      currentArtifactVersion: z.string().nullish(),
      deploymentRunId: z.string().nullish(),
      failureSourceStage: z.string().nullish(),
      feedback: z.lazy(() => StageFeedbackView).nullish(),
      highOpenQuestionCount: z.number().int().optional(),
      id: z.string(),
      inputBlueprintRef: z.lazy(() => BlueprintRefView).nullish(),
      inputCompleteness: z.lazy(() => InputCompletenessView).nullish(),
      lastRunAt: z.string().nullish(),
      openQuestionCount: z.number().int().optional(),
      outputArtifactIds: z.array(z.string()).optional(),
      questions: z.array(z.lazy(() => QuestionView)).optional(),
      stageRuns: z.array(z.lazy(() => StageRunView)).optional(),
      stageType: z.string(),
      startedAt: z.string().nullish(),
      status: z.string(),
      workflowId: z.string().nullish(),
    });

export const StageFeedbackView = z.object({
      budgetStatus: z.record(z.string(), z.unknown()).optional(),
      createdAt: z.string().nullish(),
      denialReasons: z.array(z.string()).optional(),
      gateId: z.string().nullish(),
      issues: z.array(z.lazy(() => IssueView)).optional(),
      kind: z.string().optional(),
      reason: z.string().optional(),
      rejectedArtifacts: z.array(z.record(z.string(), z.unknown())).optional(),
      rejectedBy: z.string().nullish(),
      sourceStage: z.string().nullish(),
    });

export const StageList = z.object({
      items: z.array(z.lazy(() => StageView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const StagePatchBody = z.object({
      patch: z.record(z.string(), z.unknown()).nullish(),
    });

export const StageQuestionBody = z.object({
      affects: z.array(z.string()).optional(),
      blocking: z.boolean().optional(),
      localId: z.string().nullish(),
      severity: z.string().optional(),
      suggestions: z.array(z.record(z.string(), z.unknown())).optional(),
      text: z.string(),
    });

export const StageRunView = z.object({
      agentRunIds: z.array(z.string()).optional(),
      attempt: z.number().int().optional(),
      buildIds: z.array(z.string()).optional(),
      executionIds: z.array(z.string()).optional(),
      id: z.string(),
      result: z.record(z.string(), z.unknown()).nullish(),
    });

export const StageView = z.object({
      activeDraftId: z.string().nullish(),
      artifactCount: z.number().int().optional(),
      attempt: z.number().int().optional(),
      blockingIssueCount: z.number().int().optional(),
      completedAt: z.string().nullish(),
      currentArtifactVersion: z.string().nullish(),
      deploymentRunId: z.string().nullish(),
      failureSourceStage: z.string().nullish(),
      feedback: z.lazy(() => StageFeedbackView).nullish(),
      highOpenQuestionCount: z.number().int().optional(),
      id: z.string(),
      inputBlueprintRef: z.lazy(() => BlueprintRefView).nullish(),
      inputCompleteness: z.lazy(() => InputCompletenessView).nullish(),
      lastRunAt: z.string().nullish(),
      openQuestionCount: z.number().int().optional(),
      outputArtifactIds: z.array(z.string()).optional(),
      stageType: z.string(),
      startedAt: z.string().nullish(),
      status: z.string(),
      workflowId: z.string().nullish(),
    });

export const SubmitApprovalView = z.object({
      artifacts: z.record(z.string(), z.string()).nullish(),
      blocking: z.number().int().nullish(),
      buildId: z.string().nullish(),
      draftHash: z.string().nullish(),
      gateId: z.string().nullish(),
      issues: z.array(z.lazy(() => IssueView)).optional(),
      stageId: z.string().nullish(),
      status: z.string().nullish(),
    });

export const SuggestionView = z.object({
      changesUid: z.boolean().nullish(),
      columnMapping: z.record(z.string(), z.string()).nullish(),
      id: z.string(),
      rationale: z.string().nullish(),
      recommended: z.boolean().nullish(),
      regenerates: z.array(z.string()).optional(),
      text: z.string().nullish(),
    });

export const SystemSettingItem = z.object({
      key: z.enum(["idempotency_retention_hours", "event_retention_days", "default_gate_reminder_hours", "max_upload_bytes", "allowed_upload_types", "archived_project_retention_days", "platform_notification_channels", "stage_auto_continue"] as const),
      value: z.record(z.string(), z.unknown()).optional(),
    });

export const SystemSettingList = z.object({
      items: z.array(z.lazy(() => SystemSettingItem)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
      values: z.lazy(() => SystemSettingsView).optional(),
    });

export const SystemSettingsBody = z.object({
      allowedUploadTypes: z.record(z.string(), z.unknown()).optional(),
      archivedProjectRetentionDays: z.record(z.string(), z.unknown()).optional(),
      defaultGateReminderHours: z.record(z.string(), z.unknown()).optional(),
      eventRetentionDays: z.record(z.string(), z.unknown()).optional(),
      idempotencyRetentionHours: z.record(z.string(), z.unknown()).optional(),
      maxUploadBytes: z.record(z.string(), z.unknown()).optional(),
      platformNotificationChannels: z.record(z.string(), z.unknown()).optional(),
      settings: z.record(z.string(), z.unknown()).nullish(),
      stageAutoContinue: z.record(z.string(), z.unknown()).optional(),
    });

export const SystemSettingsView = z.object({
      allowedUploadTypes: z.array(z.string()).optional(),
      archivedProjectRetentionDays: z.number().int().optional(),
      defaultGateReminderHours: z.number().int().optional(),
      eventRetentionDays: z.number().int().optional(),
      idempotencyRetentionHours: z.number().int().optional(),
      maxUploadBytes: z.number().int().optional(),
      platformNotificationChannels: z.array(z.lazy(() => NotificationChannels)).optional(),
      stageAutoContinue: z.array(z.string()).optional(),
    });

export const TargetEnvironmentBody = z.object({
      environmentKey: z.string(),
      isDesignReference: z.boolean().optional(),
      promotionOrder: z.number().int().optional(),
    });

export const TargetEnvironmentListBody = z.object({
      targetEnvironments: z.array(z.lazy(() => TargetEnvironmentBody)).optional(),
    });

export const TargetEnvironmentListView = z.object({
      items: z.array(z.lazy(() => TargetEnvironmentView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const TargetEnvironmentReplaceView = z.object({
      designReference: z.string().nullish(),
      designReferenceChanged: z.boolean().optional(),
      invalidatedStages: z.array(z.string()).optional(),
      previousDesignReference: z.string().nullish(),
      targetEnvironments: z.array(z.lazy(() => TargetEnvironmentView)).optional(),
    });

export const TargetEnvironmentView = z.object({
      environmentClass: z.string().nullish(),
      environmentKey: z.string(),
      isDesignReference: z.boolean().optional(),
      promotionOrder: z.number().int().optional(),
    });

export const TestConnectionRequest = z.object({
      requiredScopes: z.array(z.string()).nullish(),
    });

export const TraceApprovalView = z.object({
      approverName: z.string().nullish(),
      decision: z.string().nullish(),
    });

export const UidPoolReplenishRequest = z.object({
      environmentKey: z.string().nullish(),
    });

export const UidPoolView = z.object({
      available: z.number().int().optional(),
      lowWaterMark: z.number().int().optional(),
      needsReplenishment: z.boolean().optional(),
      projectId: z.string().nullish(),
      sourceEnvironment: z.string().nullish(),
    });

export const UnresolvedReferenceView = z.object({
      field: z.string().optional(),
      fromBusinessKey: z.string().optional(),
      target: z.string().optional(),
    });

export const UpdateEnvironmentRequest = z.object({
      authMode: z.string().nullish(),
      baseUrl: z.string().nullish(),
      credentials: z.array(z.lazy(() => CredentialSpec)).nullish(),
      environmentClass: z.string().nullish(),
      llmDataPolicy: z.string().nullish(),
      name: z.string().nullish(),
      notes: z.string().nullish(),
      protection: z.lazy(() => ProtectionSpec).nullish(),
      timeoutSeconds: z.number().int().nullish(),
      tlsRequired: z.boolean().nullish(),
      versionConstraint: z.string().nullish(),
    });

export const UpdateProjectBody = z.object({
      defaultLanguage: z.string().nullish(),
      description: z.string().nullish(),
      name: z.string().nullish(),
      ownerName: z.string().nullish(),
      projectType: z.string().nullish(),
      translationLanguages: z.array(z.string()).nullish(),
      versionConstraints: z.string().nullish(),
    });

export const UpdatedIdsView = z.object({
      projectId: z.string().nullish(),
      updated: z.array(z.string()).optional(),
    });

export const UpgradeModeView = z.object({
      deliveryMode: z.string().nullish(),
      invalidation: z.record(z.string(), z.unknown()).optional(),
      projectId: z.string(),
    });

export const UploadInputView = z.object({
      artifactId: z.string(),
      contentHash: z.string().nullish(),
      dataClassification: z.string().nullish(),
      filename: z.string().nullish(),
      mediaType: z.string().nullish(),
      sizeBytes: z.number().int().optional(),
      stageId: z.string().nullish(),
    });

export const UploadLimitsView = z.object({
      allowedUploadTypes: z.array(z.string()).optional(),
      maxUploadBytes: z.number().int(),
    });

export const UsageItemView = z.object({
      agentType: z.string().nullish(),
      calls: z.number().int().optional(),
      estimatedCost: z.number().optional(),
      failedCalls: z.number().int().optional(),
      inputTokens: z.number().int().optional(),
      model: z.string().nullish(),
      outputTokens: z.number().int().optional(),
      projectId: z.string().nullish(),
      provider: z.string().nullish(),
      stage: z.string().nullish(),
      workflowId: z.string().nullish(),
    });

export const UsageView = z.object({
      dimensions: z.array(z.string()).optional(),
      groupBy: z.array(z.string()).optional(),
      items: z.array(z.lazy(() => UsageItemView)).optional(),
    });

export const UserCreateRequest = z.object({
      displayName: z.string().optional(),
      email: z.string().optional(),
      mustChangePassword: z.boolean().optional(),
      password: z.string().nullish(),
      source: z.string().optional(),
      subject: z.string(),
    });

export const UserList = z.object({
      items: z.array(z.lazy(() => UserView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const UserPatchRequest = z.object({
      displayName: z.string().nullish(),
      email: z.string().nullish(),
      status: z.string().nullish(),
    });

export const UserView = z.object({
      createdAt: z.string().nullish(),
      displayName: z.string().optional(),
      email: z.string().optional(),
      id: z.string(),
      lastLoginAt: z.string().nullish(),
      locked: z.boolean().optional(),
      mustChangePassword: z.boolean().optional(),
      roles: z.array(z.lazy(() => RoleAssignmentView)).optional(),
      source: z.string().optional(),
      status: z.string().optional(),
      subject: z.string(),
    });

export const ValidationErrorField = z.object({
      message: z.string(),
      path: z.string(),
    });

export const ValidationIssuePage = z.object({
      items: z.array(z.lazy(() => IssueView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const ValidationSlot = z.object({
      blockingCount: z.number().int().optional(),
      dhis2ValidatePassed: z.boolean().optional(),
      reportArtifactId: z.string().nullish(),
      staticPassed: z.boolean().optional(),
      warningCount: z.number().int().optional(),
    });

export const VerificationSlot = z.object({
      consistent: z.boolean().nullish(),
      lastVerifiedAt: z.string().nullish(),
      postVerifyArtifactId: z.string().nullish(),
    });

export const VersionPage = z.object({
      items: z.array(z.lazy(() => VersionView)).optional(),
      limit: z.number().int().nullish(),
      nextCursor: z.string().nullish(),
      offset: z.number().int().nullish(),
      total: z.number().int().nullish(),
    });

export const VersionView = z.object({
      artifactId: z.string().nullish(),
      blueprintId: z.string().nullish(),
      changeLevel: z.string().nullish(),
      contentHash: z.string().nullish(),
      createdAt: z.string().nullish(),
      createdBy: z.string().nullish(),
      decisionIds: z.array(z.string()).optional(),
      parentVersion: z.string().nullish(),
      projectId: z.string().nullish(),
      promotedFromStage: z.string().nullish(),
      schemaVersion: z.number().int().nullish(),
      sourceArtifactIds: z.array(z.string()).optional(),
      status: z.string().nullish(),
      version: z.string(),
    });

export const WarningDisposition = z.object({
      disposition: z.string().nullish(),
      issueId: z.string().nullish(),
      note: z.string().nullish(),
    });

export const WorkflowFlagView = z.object({
      cancelled: z.boolean().nullish(),
      paused: z.boolean().nullish(),
      status: z.string().nullish(),
      workflowId: z.string(),
    });

export const WorkflowObjectGraphView = z.object({
      blueprintVersion: z.string().optional(),
      buildId: z.string().optional(),
      edges: z.array(z.lazy(() => GraphEdgeView)).optional(),
      environmentKey: z.string().nullish(),
      filters: z.lazy(() => GraphFiltersView).optional(),
      kind: z.string().optional(),
      nodes: z.array(z.lazy(() => GraphNodeView)).optional(),
      orphans: z.array(z.string()).optional(),
      projectId: z.string().optional(),
      runId: z.string().nullish(),
      schemaVersion: z.number().int().optional(),
      unresolvedRegistryRefs: z.array(z.lazy(() => UnresolvedReferenceView)).optional(),
      workflowId: z.string(),
    });

export const WorkflowView = z.object({
      activeDraft: z.lazy(() => ActiveDraftView).nullish(),
      cancelled: z.boolean().optional(),
      currentStageId: z.string().nullish(),
      deploymentRuns: z.array(z.lazy(() => RunView)).optional(),
      id: z.string(),
      lastEventSeq: z.number().int().optional(),
      milestoneStatus: z.string().nullish(),
      paused: z.boolean().optional(),
      projectId: z.string(),
      stages: z.array(z.lazy(() => ProjectStageView)).optional(),
      status: z.string(),
      version: z.number().int().optional(),
    });

export const factory_control__rest__schemas__inputs__CancelRunView = z.object({
      runId: z.string(),
      status: z.string().optional(),
    });

export const factory_control__rest__schemas__runs__CancelRunView = z.object({
      deploymentRunId: z.string(),
      status: z.string(),
    });

/** Runtime lookup: S1 component schema name → its generated Zod schema. */
export const zodSchemas: Record<string, z.ZodTypeAny> = {
  "AcceptanceView": AcceptanceView,
  "ActionEntry": ActionEntry,
  "ActivatePromptRequest": ActivatePromptRequest,
  "ActivationList": ActivationList,
  "ActivationView": ActivationView,
  "ActiveDraftView": ActiveDraftView,
  "AgentListView": AgentListView,
  "AgentProgressView": AgentProgressView,
  "AgentRunView": AgentRunView,
  "AgentSpecView": AgentSpecView,
  "AllowedActionsView": AllowedActionsView,
  "AnswerQuestionBody": AnswerQuestionBody,
  "AnswerQuestionsBody": AnswerQuestionsBody,
  "AnswerView": AnswerView,
  "ApprovalConfirmation": ApprovalConfirmation,
  "ApprovalDecision": ApprovalDecision,
  "ApprovalMismatch": ApprovalMismatch,
  "ApprovalSlot": ApprovalSlot,
  "ApprovalView": ApprovalView,
  "ApproveGateBody": ApproveGateBody,
  "ArtifactDiffView": ArtifactDiffView,
  "ArtifactPage": ArtifactPage,
  "ArtifactPurgeView": ArtifactPurgeView,
  "ArtifactSlot": ArtifactSlot,
  "ArtifactView": ArtifactView,
  "AuditPage": AuditPage,
  "AuditRecordView": AuditRecordView,
  "AuditVerifyBody": AuditVerifyBody,
  "AuditVerifyView": AuditVerifyView,
  "BindingPage": BindingPage,
  "BindingSetRequest": BindingSetRequest,
  "BindingView": BindingView,
  "BlueprintDiffView": BlueprintDiffView,
  "BlueprintRefView": BlueprintRefView,
  "Body_upload_input_api_v1_projects__project_id__inputs_post": Body_upload_input_api_v1_projects__project_id__inputs_post,
  "BoundObjectView": BoundObjectView,
  "BudgetSetRequest": BudgetSetRequest,
  "BudgetView": BudgetView,
  "CandidatePage": CandidatePage,
  "CandidateView": CandidateView,
  "CapabilityProfileView": CapabilityProfileView,
  "ChangeRequestImpactView": ChangeRequestImpactView,
  "ChangeRequestList": ChangeRequestList,
  "ChangeRequestView": ChangeRequestView,
  "ChangeStatsView": ChangeStatsView,
  "ChatClearedView": ChatClearedView,
  "ChatQuestionView": ChatQuestionView,
  "ChatTranscriptView": ChatTranscriptView,
  "ChatTurnItemView": ChatTurnItemView,
  "ChatTurnRequest": ChatTurnRequest,
  "ChatTurnView": ChatTurnView,
  "ClearedView": ClearedView,
  "ColumnMappingProposalView": ColumnMappingProposalView,
  "ColumnMappingView": ColumnMappingView,
  "CommitTraceView": CommitTraceView,
  "CommitWindow": CommitWindow,
  "CommitWindowSlot": CommitWindowSlot,
  "CommitWindowSpec": CommitWindowSpec,
  "CommitWindowView": CommitWindowView,
  "ConfirmationItemView": ConfirmationItemView,
  "ConnectionCheckItemView": ConnectionCheckItemView,
  "ConnectionCheckView": ConnectionCheckView,
  "ConnectionSlot": ConnectionSlot,
  "CreateChangeRequestBody": CreateChangeRequestBody,
  "CreateEnvironmentRequest": CreateEnvironmentRequest,
  "CreateProjectBody": CreateProjectBody,
  "CreateRunBody": CreateRunBody,
  "CreateRunView": CreateRunView,
  "CredentialSpec": CredentialSpec,
  "CredentialView": CredentialView,
  "DataClassification": DataClassification,
  "DecisionList": DecisionList,
  "DecisionView": DecisionView,
  "DeleteAcceptedView": DeleteAcceptedView,
  "DiffObjectView": DiffObjectView,
  "DisableEnvironmentRequest": DisableEnvironmentRequest,
  "DisableResultView": DisableResultView,
  "DiscoveryResultPage": DiscoveryResultPage,
  "DiscoveryResultView": DiscoveryResultView,
  "DraftView": DraftView,
  "DriftSlot": DriftSlot,
  "EffectivePolicyView": EffectivePolicyView,
  "EmergencyLockRequest": EmergencyLockRequest,
  "EmergencyLockView": EmergencyLockView,
  "EntryPage": EntryPage,
  "EnvironmentPage": EnvironmentPage,
  "EnvironmentProtectionView": EnvironmentProtectionView,
  "EnvironmentView": EnvironmentView,
  "ErrorDetails": ErrorDetails,
  "EvalCaseView": EvalCaseView,
  "EvaluationList": EvaluationList,
  "EvaluationView": EvaluationView,
  "ExecutionReportView": ExecutionReportView,
  "ExecutionSlot": ExecutionSlot,
  "ExecutionView": ExecutionView,
  "ExemptionCreateRequest": ExemptionCreateRequest,
  "ExemptionList": ExemptionList,
  "ExemptionView": ExemptionView,
  "ExpiringCredentialList": ExpiringCredentialList,
  "ExpiringCredentialView": ExpiringCredentialView,
  "ExplainRequest": ExplainRequest,
  "ExplainResultView": ExplainResultView,
  "FreshnessSlot": FreshnessSlot,
  "FrontendPackageFileView": FrontendPackageFileView,
  "FrontendPackageFilesView": FrontendPackageFilesView,
  "GateDecisionView": GateDecisionView,
  "GateExplanationView": GateExplanationView,
  "GateFoundView": GateFoundView,
  "GateList": GateList,
  "GateRejectionView": GateRejectionView,
  "GateSummaryList": GateSummaryList,
  "GateSummaryView": GateSummaryView,
  "GateUncertainView": GateUncertainView,
  "GateView": GateView,
  "GateWarningView": GateWarningView,
  "GateWillDoView": GateWillDoView,
  "GrantCreateRequest": GrantCreateRequest,
  "GrantList": GrantList,
  "GrantView": GrantView,
  "GraphEdgeView": GraphEdgeView,
  "GraphFiltersView": GraphFiltersView,
  "GraphNodeView": GraphNodeView,
  "ImpactView": ImpactView,
  "ImportProjectBody": ImportProjectBody,
  "InputCompletenessView": InputCompletenessView,
  "InputPage": InputPage,
  "InputView": InputView,
  "InvalidationPlanView": InvalidationPlanView,
  "IssueView": IssueView,
  "LineageView": LineageView,
  "LlmExchangeDetailView": LlmExchangeDetailView,
  "LlmExchangeImageView": LlmExchangeImageView,
  "LlmExchangeMessageView": LlmExchangeMessageView,
  "LlmExchangeRequestView": LlmExchangeRequestView,
  "LlmExchangeResponseView": LlmExchangeResponseView,
  "LlmExchangeSummaryView": LlmExchangeSummaryView,
  "LlmLogView": LlmLogView,
  "LoginRequest": LoginRequest,
  "ManagedObjectView": ManagedObjectView,
  "ManagedScopePage": ManagedScopePage,
  "ManualCheckBody": ManualCheckBody,
  "ManualCheckView": ManualCheckView,
  "MarkReadView": MarkReadView,
  "MemberList": MemberList,
  "MemberPutRequest": MemberPutRequest,
  "MemberView": MemberView,
  "MissingCheckView": MissingCheckView,
  "ModelCapabilities": ModelCapabilities,
  "ModelList": ModelList,
  "ModelView": ModelView,
  "NotificationChannels": NotificationChannels,
  "NotificationPage": NotificationPage,
  "NotificationView": NotificationView,
  "NrrItemView": NrrItemView,
  "NrrPage": NrrPage,
  "ObjectDeletionRequest": ObjectDeletionRequest,
  "ObjectDeletionView": ObjectDeletionView,
  "ObservationBaselineView": ObservationBaselineView,
  "OidcExchangeRequest": OidcExchangeRequest,
  "OidcRequestView": OidcRequestView,
  "PackageFileView": PackageFileView,
  "PackageFilesView": PackageFilesView,
  "ParseInputRequest": ParseInputRequest,
  "ParseOptions": ParseOptions,
  "PasswordSetRequest": PasswordSetRequest,
  "PatchModelRequest": PatchModelRequest,
  "PatchProviderRequest": PatchProviderRequest,
  "PatchSubmitView": PatchSubmitView,
  "PayloadSlot": PayloadSlot,
  "PolicyView": PolicyView,
  "ProbeResultView": ProbeResultView,
  "ProjectPage": ProjectPage,
  "ProjectPolicyView": ProjectPolicyView,
  "ProjectSettingsBody": ProjectSettingsBody,
  "ProjectSettingsView": ProjectSettingsView,
  "ProjectStageView": ProjectStageView,
  "ProjectView": ProjectView,
  "PromptDetailView": PromptDetailView,
  "PromptList": PromptList,
  "PromptListView": PromptListView,
  "ProtectionSpec": ProtectionSpec,
  "ProviderCatalogueEntry": ProviderCatalogueEntry,
  "ProviderCatalogueView": ProviderCatalogueView,
  "ProviderHealthView": ProviderHealthView,
  "ProviderList": ProviderList,
  "ProviderView": ProviderView,
  "QuestionAnswerView": QuestionAnswerView,
  "QuestionCreatedView": QuestionCreatedView,
  "QuestionList": QuestionList,
  "QuestionPage": QuestionPage,
  "QuestionSourceView": QuestionSourceView,
  "QuestionView": QuestionView,
  "QuestionnaireAnswerItem": QuestionnaireAnswerItem,
  "QuestionnaireAnswerResult": QuestionnaireAnswerResult,
  "QuestionnaireAnswerView": QuestionnaireAnswerView,
  "ReconcileView": ReconcileView,
  "RecordRegressionRequest": RecordRegressionRequest,
  "RegisterModelRequest": RegisterModelRequest,
  "RegisterProviderRequest": RegisterProviderRequest,
  "RegistryEntryView": RegistryEntryView,
  "RegistryPage": RegistryPage,
  "RegressionList": RegressionList,
  "RegressionRecordView": RegressionRecordView,
  "RejectChangeRequestBody": RejectChangeRequestBody,
  "RejectGateBody": RejectGateBody,
  "RelaxPolicyRequest": RelaxPolicyRequest,
  "RelaxationChangeView": RelaxationChangeView,
  "RelaxationView": RelaxationView,
  "ReproduceView": ReproduceView,
  "RoleAssignmentRequest": RoleAssignmentRequest,
  "RoleAssignmentView": RoleAssignmentView,
  "RoleList": RoleList,
  "RoleView": RoleView,
  "RunDetailView": RunDetailView,
  "RunEvaluationRequest": RunEvaluationRequest,
  "RunList": RunList,
  "RunPage": RunPage,
  "RunStages": RunStages,
  "RunSummaryView": RunSummaryView,
  "RunView": RunView,
  "SecretDeletedView": SecretDeletedView,
  "SecretListView": SecretListView,
  "SecretView": SecretView,
  "SecretWriteRequest": SecretWriteRequest,
  "SectionLayoutView": SectionLayoutView,
  "SectionSchemaIndexView": SectionSchemaIndexView,
  "SectionSchemaView": SectionSchemaView,
  "SessionView": SessionView,
  "SettingsUpdateView": SettingsUpdateView,
  "SetupConfigurationView": SetupConfigurationView,
  "SetupCredentialsView": SetupCredentialsView,
  "SetupEngineView": SetupEngineView,
  "SetupEnvironmentItem": SetupEnvironmentItem,
  "SetupEnvironmentsView": SetupEnvironmentsView,
  "SetupIdentityView": SetupIdentityView,
  "SetupIntelligenceView": SetupIntelligenceView,
  "SetupProjectsView": SetupProjectsView,
  "SetupRefHolderView": SetupRefHolderView,
  "SetupStartupView": SetupStartupView,
  "SetupView": SetupView,
  "StageContextView": StageContextView,
  "StageDetailView": StageDetailView,
  "StageFeedbackView": StageFeedbackView,
  "StageList": StageList,
  "StagePatchBody": StagePatchBody,
  "StageQuestionBody": StageQuestionBody,
  "StageRunView": StageRunView,
  "StageView": StageView,
  "SubmitApprovalView": SubmitApprovalView,
  "SuggestionView": SuggestionView,
  "SystemSettingItem": SystemSettingItem,
  "SystemSettingList": SystemSettingList,
  "SystemSettingsBody": SystemSettingsBody,
  "SystemSettingsView": SystemSettingsView,
  "TargetEnvironmentBody": TargetEnvironmentBody,
  "TargetEnvironmentListBody": TargetEnvironmentListBody,
  "TargetEnvironmentListView": TargetEnvironmentListView,
  "TargetEnvironmentReplaceView": TargetEnvironmentReplaceView,
  "TargetEnvironmentView": TargetEnvironmentView,
  "TestConnectionRequest": TestConnectionRequest,
  "TraceApprovalView": TraceApprovalView,
  "UidPoolReplenishRequest": UidPoolReplenishRequest,
  "UidPoolView": UidPoolView,
  "UnresolvedReferenceView": UnresolvedReferenceView,
  "UpdateEnvironmentRequest": UpdateEnvironmentRequest,
  "UpdateProjectBody": UpdateProjectBody,
  "UpdatedIdsView": UpdatedIdsView,
  "UpgradeModeView": UpgradeModeView,
  "UploadInputView": UploadInputView,
  "UploadLimitsView": UploadLimitsView,
  "UsageItemView": UsageItemView,
  "UsageView": UsageView,
  "UserCreateRequest": UserCreateRequest,
  "UserList": UserList,
  "UserPatchRequest": UserPatchRequest,
  "UserView": UserView,
  "ValidationErrorField": ValidationErrorField,
  "ValidationIssuePage": ValidationIssuePage,
  "ValidationSlot": ValidationSlot,
  "VerificationSlot": VerificationSlot,
  "VersionPage": VersionPage,
  "VersionView": VersionView,
  "WarningDisposition": WarningDisposition,
  "WorkflowFlagView": WorkflowFlagView,
  "WorkflowObjectGraphView": WorkflowObjectGraphView,
  "WorkflowView": WorkflowView,
  "factory_control__rest__schemas__inputs__CancelRunView": factory_control__rest__schemas__inputs__CancelRunView,
  "factory_control__rest__schemas__runs__CancelRunView": factory_control__rest__schemas__runs__CancelRunView,
};
