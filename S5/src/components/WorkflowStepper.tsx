import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Icon } from './icons';
import type { StageView } from '@/api/types';

/** The eight design stages, laid out as one horizontal process instead of a second sidebar. */
export function WorkflowStepper({
  projectId,
  stages,
  currentStageId,
  activeStageId,
}: {
  projectId: string;
  stages: StageView[];
  currentStageId?: string;
  activeStageId?: string;
}) {
  const { t } = useTranslation();

  return (
    <div className="stepper-wrap">
      <ol className="stepper" aria-label={t('workspace.stageFlow')}>
        {stages.map((stage, index) => {
          const active = stage.id === activeStageId;
          const current = stage.id === currentStageId;
          const skipped = stage.status === 'SKIPPED';
          const passed = ['PASSED', 'APPROVED', 'VERIFIED', 'COMPLETED'].includes(stage.status);
          const attention =
            (stage.highOpenQuestionCount ?? 0) > 0 || (stage.blockingIssueCount ?? 0) > 0;
          return (
            <li
              key={stage.id}
              className={[
                'stepper__step',
                active ? 'is-active' : '',
                current ? 'is-current' : '',
                skipped ? 'is-skipped' : '',
                passed ? 'is-done' : '',
                attention ? 'is-attention' : '',
              ].filter(Boolean).join(' ')}
            >
              <Link
                to={`/projects/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(stage.id)}`}
                aria-current={active ? 'step' : undefined}
              >
                <span className="stepper__dot" aria-hidden="true">
                  {passed ? <Icon name="check" size={14} /> : index + 1}
                </span>
                <span className="stepper__copy">
                  <span className="stepper__label">
                    {t(`stageTypes.${stage.stageType}`, { defaultValue: stage.stageType })}
                  </span>
                  <span className="stepper__meta">
                    {skipped
                      ? t('workspace.stageSkipped')
                      : t(`stageStatus.${stage.status}`, { defaultValue: stage.status })}
                    {(stage.openQuestionCount ?? 0) > 0
                      ? ` · ${t('stepper.questions', { count: stage.openQuestionCount ?? 0 })}`
                      : ''}
                  </span>
                </span>
                {attention ? <span className="stepper__alert" aria-hidden="true">!</span> : null}
              </Link>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
