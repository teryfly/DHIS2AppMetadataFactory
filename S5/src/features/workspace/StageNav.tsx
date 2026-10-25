/*
 * UI-WB-002: every stage shows status (kept identical to S1, incl. MANUAL_CHECK), input
 * completeness, artifact count, open questions, BLOCKING issues, last run time and the current
 * artifact version. Deployment Run steps appear under Deployments, not here.
 */
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { StatusBadge } from '@/components/StatusBadge';
import { Loading } from '@/lib/PageContainer';
import { stagesOptions } from '@/api/queries';
import { formatRelativeTime } from '@/lib/time';
import type { StageView } from '@/api/types';
import { severityLabel } from '@/lib/labels';

export function StageNav({ workflowId, projectId, currentStageId }: { workflowId: string | undefined; projectId: string; currentStageId?: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const stages = useQuery(stagesOptions(workflowId));
  const items = (stages.data?.items ?? []) as StageView[];

  if (stages.isLoading) return <Loading />;

  return (
    <nav className="workbench__stages" aria-label={t('nav.projects')}>
      {items.map((s) => {
        const to = `/projects/${projectId}/stages/${s.id}`;
        const isCurrent = location.pathname === to || (currentStageId === s.id && location.pathname.endsWith('/overview'));
        return (
          <button
            key={s.id}
            type="button"
            className="stage-item"
            aria-current={isCurrent ? 'page' : undefined}
            onClick={() => navigate(to)}
          >
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong>{t(`stageTypes.${s.stageType}`, { defaultValue: s.stageType })}</strong>
            </div>
            <div style={{ marginTop: 4 }}>
              <StatusBadge status={s.status} />
            </div>
            <div className="stage-item__metrics">
              {s.inputCompleteness && (s.inputCompleteness.satisfied ?? 1) < (s.inputCompleteness.total ?? 1) ? (
                <span title={t('workspace.inputsIncomplete')}>⚠ {t('workspace.inputsIncomplete')}</span>
              ) : null}
              {(s.artifactCount ?? 0) > 0 ? <span>{t('workspace.artifacts', { count: s.artifactCount ?? 0 })}</span> : null}
              {(s.openQuestionCount ?? 0) > 0 ? <span title={t('questions.title')}>{t('workspace.questionsOpen', { count: s.openQuestionCount ?? 0 })}</span> : null}
              {(s.highOpenQuestionCount ?? 0) > 0 ? <span className="text-critical">{t('workspace.highOpenQuestions', { count: s.highOpenQuestionCount ?? 0, level: severityLabel('HIGH') })}</span> : null}
              {(s.blockingIssueCount ?? 0) > 0 ? <span className="text-critical">{t('workspace.questionsBlocking', { count: s.blockingIssueCount ?? 0 })}</span> : null}
            </div>
            <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
              {s.lastRunAt ? formatRelativeTime(s.lastRunAt, t) : ''}
              {s.currentArtifactVersion ? ` · v${s.currentArtifactVersion}` : ''}
            </div>
          </button>
        );
      })}
    </nav>
  );
}
