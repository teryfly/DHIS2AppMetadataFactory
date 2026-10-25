/*
 * UI-DEP-001: Deployments lists, per project target environment (in promotion order), each
 * environment's current run status, its Blueprint version, whether it is OUTDATED and the last
 * VERIFIED time; a run can be created for an environment (availability from allowed-actions).
 */
import { useTranslation } from 'react-i18next';
import { Link, useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, Button } from '@dhis2/ui';
import { StatusBadge } from '@/components/StatusBadge';
import { AsyncShell } from '@/components/AsyncShell';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { deploymentRunsOptions } from '@/api/queries';
import { useCreateRun } from '@/api/mutations';
import { actionIntent } from '@/api/idempotency';
import { formatRelativeTime } from '@/lib/time';
import { ACTION } from '@/api/actions';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';

export function DeploymentsView() {
  const { t } = useTranslation();
  const ctx = useOutletContext<WorkspaceContext>();
  const { projectId, workflowId } = ctx;
  const runs = useQuery(deploymentRunsOptions(workflowId));
  const createRun = useCreateRun(workflowId ?? '');
  const targets = [...(ctx.project.targetEnvironments ?? [])].sort((a, b) => (a.promotionOrder ?? 0) - (b.promotionOrder ?? 0));
  const runFor = (envKey: string) => (runs.data?.items ?? []).find((r) => r.environmentKey === envKey);

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h3 style={{ margin: 0 }}>{t('deployments.byEnvironment')}</h3>
      </div>
      {/* A project with no target environment is a normal state of a new project, not a broken
          screen — and the run list arriving empty because S1 refused it is the other one. Both
          answers came from the page's own markup until now. */}
      <AsyncShell
        loading={runs.isLoading}
        error={runs.error}
        empty={!targets.length}
        emptyTitle={t('deployments.noTargets')}
        emptyHint={t('deployments.noTargetsHint')}
        emptyAction={
          <Link to={`/projects/${encodeURIComponent(projectId)}/settings`}>{t('nav.projectSettings')}</Link>
        }
        onRetry={() => void runs.refetch()}
      >
        {targets.map((env) => {
          const run = runFor(env.environmentKey);
          return (
            <Card key={env.environmentKey}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <div className="stack" style={{ gap: 4 }}>
                  <strong>
                    {env.environmentKey}
                    {env.isDesignReference ? ' ★' : ''} · {t(`environments.class${env.environmentClass ?? 'TEST'}`, { defaultValue: env.environmentClass ?? '' })}
                  </strong>
                  {run ? (
                    <span className="row" style={{ gap: 8 }}>
                      <StatusBadge status={run.status} />
                      <span className="muted" style={{ fontSize: 12 }}>{t('deployments.blueprintVersion')}: {run.blueprintVersion ?? '—'}</span>
                      {run.outdated ? <span className="text-warning">{t('deployments.outdated')}</span> : null}
                      <span className="muted" style={{ fontSize: 12 }}>{t('deployments.lastVerified')}: {formatRelativeTime(run.lastVerifiedAt, t)}</span>
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </div>
                <div className="row">
                  <AllowedActionButton
                    workflowId={workflowId}
                    action={ACTION.deploymentCreate}
                    targetId={env.environmentKey}
                    onExecute={() => createRun.mutateAsync({ body: { environmentKey: env.environmentKey }, intent: actionIntent('POST', '/deployment-runs', env.environmentKey) })}
                    loading={createRun.isPending}
                  >
                    {t('deployments.createRun')}
                  </AllowedActionButton>
                  {run ? <Link to={`/projects/${projectId}/deployments/${run.id}`}><Button secondary>{t('deployments.console')}</Button></Link> : null}
                </div>
              </div>
            </Card>
          );
        })}
      </AsyncShell>
    </div>
  );
}
