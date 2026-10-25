import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Button } from '@dhis2/ui';
import { NoticeBar } from '@/components/NoticeBar';
import { setupOptions } from '@/api/queries';
import { ACTION, useAllowedActions } from '@/api/actions';
import { actionReasonText } from '@/components/AllowedActionButton';
import { setupSteps } from './SetupView';

/**
 * The first blocking step, shown on the Dashboard until the configuration is complete
 * (UI-SET-001). It is the same branch table as the getting-started page, over the same facts, so a
 * Dashboard that says "ready" and a checklist that says "not configured" cannot coexist.
 */
export function SetupSummary() {
  const { t } = useTranslation();
  const setup = useQuery(setupOptions());
  const { check } = useAllowedActions(undefined, 'platform');

  if (!setup.data) return null;
  const create = check(ACTION.projectCreate);
  const steps = setupSteps(setup.data, { available: create.available, reason: actionReasonText(t, create) });
  // `pipeline` is the project's own business, and `unknown` means the facts are not this caller's
  // to see — neither is something the Dashboard should nag about.
  const blocking = steps.filter((step) => step.id !== 'pipeline' && (step.state === 'missing' || step.state === 'partial'));
  if (blocking.length === 0) return null;

  const first = blocking[0]!;
  return (
    <NoticeBar tone="warning" title={t('dashboard.setupPending', { n: blocking.length })}>
      <div className="stack" style={{ fontSize: 13 }}>
        <span>
          {t(`setup.step.${first.id}.title`)} — {t(first.consequence.key, first.consequence.options)}
        </span>
        <Link to="/getting-started">
          <Button secondary small>{t('dashboard.setupOpen')}</Button>
        </Link>
      </div>
    </NoticeBar>
  );
}
