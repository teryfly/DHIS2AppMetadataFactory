import { lazy, Suspense, type ComponentType } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppShell } from '@/components/AppShell';
import { SessionGate } from '@/components/SessionGate';
import { Loading } from '@/lib/PageContainer';

/*
 * Every screen behind its own chunk (UI-NFR-02).
 *
 * The frame — shell, session gate, and the two halves of the sign-in round trip, which is the first
 * thing an operator sees on an account deployment — stays in the entry because nothing can render
 * without it. Everything else is fetched when a route asks for it, so first paint on a factory
 * laptop no longer depends on how many screens the console has grown. `npm run check:budget` is the
 * guard: without a number to fail against, "lazy" quietly becomes "eager" again the moment one
 * screen imports the editor or the graph library at module scope.
 */
const lazyView = (load: () => Promise<Record<string, unknown>>, name: string) =>
  lazy(() => load().then((module) => ({ default: module[name] as ComponentType })));

const LoginScreen = lazyView(() => import('@/features/auth/LoginScreen'), 'LoginScreen');
const OidcCallback = lazyView(() => import('@/features/auth/OidcCallback'), 'OidcCallback');
const Dashboard = lazyView(() => import('@/features/dashboard/Dashboard'), 'Dashboard');
const SetupSteps = lazyView(() => import('@/features/setup/SetupSteps'), 'SetupSteps');
const TutorialView = lazyView(() => import('@/features/help/TutorialView'), 'TutorialView');
const ApprovalsInbox = lazyView(() => import('@/features/approvals/ApprovalsInbox'), 'ApprovalsInbox');
const ProjectsList = lazyView(() => import('@/features/projects/ProjectsList'), 'ProjectsList');
const NewProjectWizard = lazyView(() => import('@/features/projects/NewProjectWizard'), 'NewProjectWizard');
const ProjectSettingsView = lazyView(() => import('@/features/projects/ProjectSettingsView'), 'ProjectSettingsView');
const ProjectWorkspace = lazyView(() => import('@/features/workspace/ProjectWorkspace'), 'ProjectWorkspace');
const Overview = lazyView(() => import('@/features/workspace/Overview'), 'Overview');
const StageView = lazyView(() => import('@/features/workspace/StageView'), 'StageView');
const GatePage = lazyView(() => import('@/features/workspace/gate/GatePage'), 'GatePage');
const InputsView = lazyView(() => import('@/features/inputs/InputsView'), 'InputsView');
const ColumnMappingView = lazyView(() => import('@/features/inputs/ColumnMappingView'), 'ColumnMappingView');
const LlmLogView = lazyView(() => import('@/features/inputs/LlmLogView'), 'LlmLogView');
const ArtifactReview = lazyView(() => import('@/features/review/ArtifactReview'), 'ArtifactReview');
const ValidationIssues = lazyView(() => import('@/features/review/ValidationIssues'), 'ValidationIssues');
const MetadataReview = lazyView(() => import('@/features/review/MetadataReview'), 'MetadataReview');
const DecisionsView = lazyView(() => import('@/features/review/DecisionsView'), 'DecisionsView');
const ObjectGraph = lazyView(() => import('@/features/review/ObjectGraph'), 'ObjectGraph');
const ApiReviewView = lazyView(() => import('@/features/review/ApiReview'), 'ApiReviewView');
const ApiDocView = lazyView(() => import('@/features/review/ApiReview'), 'ApiDocView');
const HandoffView = lazyView(() => import('@/features/review/HandoffView'), 'HandoffView');
const DeploymentsView = lazyView(() => import('@/features/deployments/DeploymentsView'), 'DeploymentsView');
const RunConsole = lazyView(() => import('@/features/deployments/RunConsole'), 'RunConsole');
const AuditView = lazyView(() => import('@/features/audit/AuditView'), 'AuditView');
const ChangeRequestsView = lazyView(() => import('@/features/changeRequests/ChangeRequestsView'), 'ChangeRequestsView');
const EnvironmentsView = lazyView(() => import('@/features/environments/EnvironmentsView'), 'EnvironmentsView');
const EnvironmentDetailView = lazyView(() => import('@/features/environments/EnvironmentDetailView'), 'EnvironmentDetailView');
const GovernanceView = lazyView(() => import('@/features/governance/GovernanceView'), 'GovernanceView');
const NotificationsView = lazyView(() => import('@/features/notifications/NotificationsView'), 'NotificationsView');
const SystemSettingsView = lazyView(() => import('@/features/settings/SystemSettingsView'), 'SystemSettingsView');
const UsersView = lazyView(() => import('@/features/admin/UsersView'), 'UsersView');
const RolesView = lazyView(() => import('@/features/admin/RolesView'), 'RolesView');
const ProjectMembers = lazyView(() => import('@/features/project/ProjectMembers'), 'ProjectMembers');
const ArtifactGrants = lazyView(() => import('@/features/project/ArtifactGrants'), 'ArtifactGrants');
const ProductionExemptions = lazyView(() => import('@/features/project/ProductionExemptions'), 'ProductionExemptions');
const NotFound = lazyView(() => import('@/features/common/NotFound'), 'NotFound');

/**
 * Information architecture (UI-IA-001). The project workspace nests stage/gate/deployment/audit
 * routes under `/projects/:projectId`; the inspector and stage nav live in ProjectWorkspace.
 */
export function AppRoutes() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        {/* UI-AUTH-001: the sign-in pair sits outside the gate, which is what it signs in through. */}
        <Route path="/login" element={<LoginScreen />} />
        <Route path="/auth/callback" element={<OidcCallback />} />
        <Route element={<SessionGate />}>
          <Route element={<AppShell />}>
            <Route index element={<Dashboard />} />
            <Route path="getting-started" element={<SetupSteps />} />
            {/* the worked example the help drawer and the inputs page link to */}
            <Route path="help/tutorial" element={<TutorialView />} />
            {/* the approver's own page: cross-project, addressed, and the only place that answers
                "what is waiting on me" without first asking which project it is in */}
            <Route path="approvals" element={<ApprovalsInbox />} />
            <Route path="projects" element={<ProjectsList />} />
            <Route path="projects/new" element={<NewProjectWizard />} />
            <Route path="projects/:projectId" element={<ProjectWorkspace />}>
              <Route index element={<Navigate to="overview" replace />} />
              <Route path="overview" element={<Overview />} />
              <Route path="stages/:stageId" element={<StageView />} />
              <Route path="inputs" element={<InputsView />} />
              <Route path="artifacts" element={<ArtifactReview />} />
              {/* UI-REV-001: one artifact, one address. The same component renders both; the URL says
                  which artifact is open, so a review can be quoted instead of described. */}
              <Route path="artifacts/:artifactId" element={<ArtifactReview />} />
              <Route path="issues" element={<ValidationIssues />} />
              <Route path="metadata" element={<MetadataReview />} />
              {/* S5IF-BP13: the Registry table names a decision per row, and the link it has always
                  carried needed a page to land on */}
              <Route path="decisions" element={<DecisionsView />} />
              <Route path="graph" element={<ObjectGraph />} />
              <Route path="api" element={<ApiReviewView />} />
              <Route path="api/doc" element={<ApiDocView />} />
              {/* UI-IA-001: Handoff is a section of the workspace, not a footnote on the metadata page. */}
              <Route path="handoff" element={<HandoffView />} />
              <Route path="inputs/:artifactId/column-mapping" element={<ColumnMappingView />} />
              {/* S5IF-INP08: the model conversation behind a parse, reachable from its status. */}
              <Route path="inputs/:artifactId/llm-log" element={<LlmLogView />} />
              <Route path="gate/:gateId" element={<GatePage />} />
              <Route path="deployments" element={<DeploymentsView />} />
              <Route path="deployments/:runId" element={<RunConsole />} />
              <Route path="change-requests" element={<ChangeRequestsView />} />
              <Route path="audit" element={<AuditView />} />
              <Route path="members" element={<ProjectMembers />} />
              <Route path="grants" element={<ArtifactGrants />} />
              <Route path="exemptions" element={<ProductionExemptions />} />
              <Route path="settings" element={<ProjectSettingsView />} />
            </Route>
            <Route path="environments" element={<EnvironmentsView />} />
            <Route path="environments/:environmentKey" element={<EnvironmentDetailView />} />
            {/* one section per address; the bare path lands on the first of them (UI-IA-001's shape,
                applied to the platform's own page) */}
            <Route path="governance" element={<Navigate to="/governance/models" replace />} />
            <Route path="governance/:section" element={<GovernanceView />} />
            <Route path="users" element={<UsersView />} />
            <Route path="roles" element={<RolesView />} />
            <Route path="notifications" element={<NotificationsView />} />
            <Route path="settings" element={<SystemSettingsView />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Route>
      </Routes>
    </Suspense>
  );
}
