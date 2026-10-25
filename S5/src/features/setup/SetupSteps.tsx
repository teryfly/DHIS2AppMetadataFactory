/*
 * UI-SET-001…006 (amended at 契约 1.7.0): the getting-started path, one step per screen, with the
 * registration forms inside the steps that need them.
 *
 * Two rules shape this file. First, every line of it is a fact S1 reported — `GET /setup` for the
 * state of the deployment, `allowed-actions` for whether a control may be pressed — and nothing is
 * inferred locally (UI-BR-01, UI-BR-02); a withheld section renders as "not visible to you", never
 * as zero. Second, the forms are the same components the Administration pages render, opened inline
 * rather than as a modal, so there is exactly one definition of what a valid provider, model or
 * environment registration is (UI-SET-006). A second copy inside the guide would drift the first
 * time a server rule moved.
 *
 * A step whose work is not a form — creating the first project, running a stage — still links out,
 * because the project wizard and the workbench are pages, not dialogs, and pretending otherwise
 * would cost the operator their browser history for no benefit.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Tag } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { NoticeBar } from '@/components/NoticeBar';
import { Heading } from '@/components/typography';
import { ACTION, useAllowedActions } from '@/api/actions';
import { governanceProvidersOptions, setupOptions } from '@/api/queries';
import { ModelForm, ProviderForm } from '@/features/governance/GovernanceView';
import { EnvironmentForm } from '@/features/environments/EnvironmentsView';
import { STATE_SKIN, capabilityFacts, setupSteps, type SetupStep } from './SetupView';

/**
 * The steps that can be finished without leaving this page. `providers` is not one of them: those
 * facts are S1's subsystem wiring — whether S2, S3 and S4 are real implementations or stand-ins —
 * which is a deployment choice (`provider_mode` in the env file) and no browser control can change.
 * The vendor registration belongs to the model step, which opens it as well.
 */
const INLINE_FORMS = new Set(['models', 'environments']);

export function SetupSteps() {
  const { t } = useTranslation();
  const setup = useQuery(setupOptions());
  const providers = useQuery(governanceProvidersOptions());
  const { check } = useAllowedActions(undefined, 'platform');
  const [active, setActive] = useState<string | null>(null);
  const [form, setForm] = useState<string | null>(null);

  const steps = useMemo(
    () =>
      setupSteps(setup.data, {
        available: check(ACTION.projectCreate).available,
        reason: check(ACTION.projectCreate).available ? '' : t('setup.projectLocked'),
      }),
    [setup.data, check, t],
  );
  /** the deployment's capability ports, stated as a fact rather than asked as a task */
  const capability = capabilityFacts(setup.data);

  // the first unfinished step is where an operator who just arrived should be standing
  const currentId = active ?? steps.find((step) => step.state !== 'ready')?.id ?? steps[0]?.id;
  const current: SetupStep | undefined = steps.find((step) => step.id === currentId);

  if (setup.isLoading || !current) return <Loading label={t('app.loading')} />;

  const problems = setup.data?.configuration?.problems ?? [];
  const closeForm = () => {
    setForm(null);
    // the mutations invalidate `setup`, so the next render re-derives the step states; clearing
    // `active` lets the guide walk on to whatever is now the first unfinished step
    setActive(null);
  };

  return (
    <PageContainer title={t('setup.title')} description={t('page.setup')} helpKey="setup">
      {problems.length > 0 ? (
        <NoticeBar tone="error" title={t('setup.problemsTitle')}>
          <ul>
            {problems.map((line) => (
              // Verbatim, because the sentence names the variable to edit in `.deploy/env`; it
              // never carries a value (S5IF-G09), so there is nothing to mask here.
              <li key={line}>
                <code>{line}</code>
              </li>
            ))}
          </ul>
        </NoticeBar>
      ) : null}

      {setup.data?.identity?.pendingPasswordChange ? (
        <NoticeBar tone="warning" title={t('setup.passwordTitle')}>
          {t('setup.passwordBody')}
        </NoticeBar>
      ) : null}

      <p className="muted">{t('setup.intro')}</p>

      <div className="setup-layout">
        <ol className="setup-rail" aria-label={t('setup.title')}>
          {steps.map((step, index) => (
            <li key={step.id}>
              <button
                type="button"
                className="setup-step"
                aria-current={step.id === current.id ? 'step' : undefined}
                onClick={() => {
                  setActive(step.id);
                  setForm(null);
                }}
              >
                <span className="setup-step__index" aria-hidden="true">{index + 1}</span>
                <span className="setup-step__copy">
                  <span className="setup-step__title">{t(`setup.step.${step.id}.title`)}</span>
                  <span className="setup-step__meta">{t(`setup.state.${step.state}`)}</span>
                </span>
                <Tag
                  neutral={STATE_SKIN[step.state] === 'neutral'}
                  positive={STATE_SKIN[step.state] === 'positive'}
                  negative={STATE_SKIN[step.state] === 'negative'}
                >
                  {t(`setup.state.${step.state}`)}
                </Tag>
              </button>
            </li>
          ))}
        </ol>

        <div className="setup-panel">
      <section className="panel" style={{ padding: 16, display: 'grid', gap: 12 }} aria-live="polite">
        <Heading level={4} size="small">{t(`setup.step.${current.id}.title`)}</Heading>
        <span>{t(current.fact.key, current.fact.options)}</span>
        <p className="muted">{t(current.consequence.key, current.consequence.options)}</p>

        {/* S1 said the credential store is off. Both steps below ask for a key, so this is the
            place to say it — before the operator types something that will be refused and thrown
            away, rather than after. */}
        {setup.data?.credentials && !setup.data.credentials.storeAvailable
        && (current.id === 'models' || current.id === 'environments') ? (
          <NoticeBar tone="warning" title={t('setup.storeOffTitle')}>
            {t('setup.storeOffHere')}
          </NoticeBar>
        ) : null}

        {/* The opener is gated by allowed-actions like every other control (UI-BR-01): a step that
            S1 will not let this identity register anything for says whose refusal it is, rather than
            opening a form whose save button is dead. */}
        {current.id === 'models' ? (
          <div className="row" style={{ gap: 8 }}>
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governanceModelRegister}
              tone="primary"
              onExecute={() => setForm('models')}
            >
              {t('setup.step.models.go')}
            </AllowedActionButton>
            {/* a model hangs on a provider, so this step opens the registration it depends on as
                well rather than leaving the operator to discover the order from a 422 */}
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governanceProviderRegister}
              tone="secondary"
              onExecute={() => setForm('providers')}
            >
              {t('setup.step.models.providerGo')}
            </AllowedActionButton>
          </div>
        ) : null}

        {current.id === 'environments' ? (
          <div className="row" style={{ gap: 8 }}>
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.environmentCreate}
              tone="primary"
              onExecute={() => setForm('environments')}
            >
              {t('setup.step.environments.go')}
            </AllowedActionButton>
          </div>
        ) : null}

        {!INLINE_FORMS.has(current.id) ? <Link to={current.to}>{t(`setup.step.${current.id}.go`)}</Link> : null}

        {/* The way out stays visible while the form is open: an operator who would rather work on
            the Administration page mid-entry should not have to close this one to find it. */}
        {INLINE_FORMS.has(current.id) ? (
          <Link to={current.to}>{t('setup.openInstead')}</Link>
        ) : null}

        {form === 'providers' ? <ProviderForm provider={null} onClose={closeForm} onSaved={closeForm} embedded /> : null}
        {form === 'models' ? (
          <ModelForm providers={providers.data?.items ?? []} onClose={closeForm} onSaved={closeForm} embedded />
        ) : null}
        {form === 'environments' ? <EnvironmentForm environment={null} onClose={closeForm} onSaved={closeForm} embedded /> : null}
      </section>

      <section className="panel" style={{ padding: 16, display: 'grid', gap: 8 }}>
        <Heading level={5} size="small">{t('setup.step.providers.title')}</Heading>
        <span>{t(capability.ready ? 'setup.providersReady' : 'setup.providersFake', { mode: capability.mode, which: capability.which })}</span>
        <span className="muted">{t('setup.providersMode', { mode: capability.mode })}</span>
        {/* No control here: `provider_mode` is chosen in the server's own env file, so the guide
            reports the answer and asks the operator for nothing. */}
      </section>

      <p className="muted">{t('setup.availabilityNote')}</p>
        </div>
      </div>
    </PageContainer>
  );
}
