/*
 * UI-PRJ-002: the new-project wizard collects, in order — name & description, owner name, delivery
 * mode (A/B), project type, version constraints, default & translation languages, target
 * environments (at least one non-PRODUCTION, a design reference, promotion order) — and shows a
 * summary before submitting. RHF + a Zod model validate each step (UI-APP-007 style, extended with
 * the wizard's own cross-field rules).
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, Link } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@dhis2/ui';
import { PageContainer } from '@/lib/PageContainer';
import { Heading } from '@/components/typography';
import { TextInput, SelectInput, CheckboxInput } from '@/components/form';
import { EmptyState } from '@/components/EmptyState';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { Icon } from '@/components/icons';
import { ACTION } from '@/api/actions';
import { useCreateProject } from '@/api/mutations';
import { environmentsOptions, sessionOptions } from '@/api/queries';
import { useQuery } from '@tanstack/react-query';
import { hasOperator, useOperatorStore } from '@/stores/operator';
import { deliveryModeLabel, projectTypeLabel } from '@/lib/labels';

const targetSchema = z.object({
  environmentKey: z.string(),
  isDesignReference: z.boolean(),
  promotionOrder: z.number(),
});

const wizardSchema = z
  .object({
    name: z.string().min(1),
    description: z.string(),
    ownerName: z.string().min(1),
    deliveryMode: z.enum(['A', 'B']),
    projectType: z.enum(['GREENFIELD', 'BROWNFIELD']),
    versionConstraints: z.string(),
    defaultLanguage: z.enum(['zh-CN', 'en']),
    translationLanguages: z.array(z.string()),
    targets: z.array(targetSchema),
  })
  // Which target is PRODUCTION is a fact about the registered environment, not about this form, so
  // the two cross-field rules S1 enforces (`services/project.py` validate_request and
  // `_confirm_environments`) are checked in `validateTargets` with the environment list in hand —
  // a static Zod refine here could only pretend to know, and it used to claim a rule it did not
  // apply: `needNonProduction` was `targets.length >= 1`.
  .refine((v) => v.targets.length >= 1, { path: ['targets'], message: 'at least one target' });

type WizardValues = z.infer<typeof wizardSchema>;
type StepField = keyof WizardValues;
const STEPS: { key: string; fields: StepField[] }[] = [
  { key: 'stepName', fields: ['name', 'description'] },
  { key: 'stepOwner', fields: ['ownerName'] },
  { key: 'stepMode', fields: ['deliveryMode', 'projectType', 'versionConstraints'] },
  { key: 'stepLanguage', fields: ['defaultLanguage', 'translationLanguages'] },
  { key: 'stepTargets', fields: ['targets'] },
  { key: 'stepReview', fields: [] },
];

const DEFAULTS: WizardValues = {
  name: '',
  description: '',
  ownerName: '',
  deliveryMode: 'A',
  projectType: 'GREENFIELD',
  versionConstraints: '',
  defaultLanguage: 'en',
  translationLanguages: [],
  targets: [],
};

export function NewProjectWizard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useCreateProject();
  const envs = useQuery(environmentsOptions());
  const [step, setStep] = useState(0);

  const form = useForm<WizardValues>({
    resolver: zodResolver(wizardSchema),
    defaultValues: DEFAULTS,
    mode: 'onBlur',
  });
  const targets = form.watch('targets');

  // The owner is a person, and under M4 that person is the signed-in account — S1 records the
  // subject from the session whatever the header claims. On a cold load the session is still in
  // flight, so keep following it until the field is the user's own words.
  const operatorName = useOperatorStore((s) => s.name);
  const session = useQuery(sessionOptions());
  const me = session.data?.displayName || session.data?.subject || operatorName || '';
  useEffect(() => {
    if (!me || form.formState.dirtyFields.ownerName) return;
    if (form.getValues('ownerName') !== me) form.setValue('ownerName', me);
  }, [me, form]);

  const environmentOptions = (envs.data?.items ?? []).map((e) => ({
    value: e.environmentKey,
    label: `${e.name ?? e.environmentKey} · ${t(`environments.class${e.environmentClass}`, { defaultValue: e.environmentClass })}`,
    isProduction: e.environmentClass === 'PRODUCTION',
  }));
  const productionKeys = new Set(environmentOptions.filter((o) => o.isProduction).map((o) => o.value));
  const noEnvironments = !envs.isLoading && environmentOptions.length === 0;

  /**
   * The two rules S1 will refuse the create over, checked here with the environment list in hand so
   * the operator is told before submitting (CP-PRJ-002: exactly one design reference, and at least
   * one target that is not PRODUCTION).
   */
  const validateTargets = (): boolean => {
    const picked = form.getValues('targets');
    const message =
      picked.length === 0
        ? t('wizard.needTargets')
        : picked.filter((x) => x.isDesignReference).length !== 1
          ? t('wizard.needOneDesignReference')
          : picked.every((x) => productionKeys.has(x.environmentKey))
            ? t('wizard.needNonProduction')
            : undefined;
    if (message) {
      form.setError('targets', { message });
      return false;
    }
    form.clearErrors('targets');
    return true;
  };

  const goNext = async () => {
    const fields = STEPS[step]?.fields ?? [];
    const shaped = fields.length === 0 ? true : await form.trigger(fields as never);
    // A failed trigger has to be visible: this step used to leave the Next button doing nothing at
    // all, because the error it raised was rendered nowhere (UI-SET-004).
    const targetsOk = step === 4 ? validateTargets() : true;
    if (shaped && targetsOk) setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  const addEnvironment = (environmentKey: string) => {
    if (targets.some((x) => x.environmentKey === environmentKey)) return;
    form.setValue('targets', [
      ...targets,
      { environmentKey, isDesignReference: targets.length === 0, promotionOrder: targets.length + 1 },
    ]);
  };

  const toggleDesignRef = (environmentKey: string) => {
    form.setValue('targets', targets.map((x) => ({ ...x, isDesignReference: x.environmentKey === environmentKey })));
  };

  const moveTarget = (index: number, dir: -1 | 1) => {
    const next = [...targets];
    const j = index + dir;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j]!, next[index]!];
    form.setValue('targets', next.map((x, i) => ({ ...x, promotionOrder: i + 1 })));
  };

  const submit = () => {
    if (!validateTargets()) return;
    void form.handleSubmit((values) => {
      create.mutate(
        {
          body: {
            name: values.name,
            description: values.description,
            ownerName: values.ownerName,
            deliveryMode: values.deliveryMode,
            projectType: values.projectType,
            versionConstraints: values.versionConstraints,
            defaultLanguage: values.defaultLanguage,
            translationLanguages: values.translationLanguages,
            targetEnvironments: values.targets,
            targets: values.targets,
          } as never,
        },
        {
          onSuccess: (project) => navigate(`/projects/${project.id}/overview`),
        },
      );
    })();
  };

  const currentFields = STEPS[step]?.fields ?? [];
  const targetsError = form.formState.errors.targets?.message as string | undefined;

  return (
    <PageContainer title={t('wizard.title')} description={t('page.newProject')} helpKey="projects">
      <div className="wizard-layout">
        <ol className="wizard-rail" aria-label={t('wizard.stepsLabel')}>
          {STEPS.map((entry, index) => (
            <li key={entry.key}>
              <button
                type="button"
                className={`wizard-step${index < step ? ' is-done' : ''}`}
                aria-current={index === step ? 'step' : undefined}
                onClick={() => setStep(index)}
                disabled={index > step}
              >
                <span className="wizard-step__index" aria-hidden="true">
                  {index < step ? <Icon name="check" size={13} /> : index + 1}
                </span>
                <span className="wizard-step__copy">
                  <span className="wizard-step__title">{t(`wizard.${entry.key}`)}</span>
                  <span className="wizard-step__meta">
                    {index < step
                      ? t('wizard.stepDone')
                      : index === step
                        ? t('wizard.stepCurrent')
                        : t('wizard.stepPending')}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ol>

        <div className="wizard-panel">
      <div className="panel" style={{ padding: 20 }}>
        <Heading level="4" size="small" className="muted">
          {t(`wizard.${STEPS[step]!.key}`)} · {step + 1}/{STEPS.length}
        </Heading>
        <div className="stack" style={{ marginTop: 16 }}>
          {step === 0 && (
            <>
              <LabeledField form={form} name="name" label={t('wizard.basicsNameLabel')} required>
                {(p) => <TextInput label={t('wizard.basicsNameLabel')} value={p.value} onChange={p.onChange} error={p.error} required />}
              </LabeledField>
              <LabeledField form={form} name="description" label={t('wizard.basicsDescLabel')}>
                {(p) => <TextInput label={t('wizard.basicsDescLabel')} value={p.value} onChange={p.onChange} error={p.error} />}
              </LabeledField>
            </>
          )}

          {step === 1 && (
            <LabeledField form={form} name="ownerName" label={t('wizard.ownerNameLabel')} required>
              {(p) => <TextInput label={t('wizard.ownerNameLabel')} value={p.value} onChange={p.onChange} error={p.error} required />}
            </LabeledField>
          )}

          {step === 2 && (
            <>
              <LabeledField form={form} name="deliveryMode" label={t('wizard.modeLabel')}>
                {(p) => (
                  <SelectInput
                    label={t('wizard.modeLabel')}
                    value={p.value}
                    onChange={p.onChange}
                    error={p.error}
                    options={[
                      { value: 'A', label: t('wizard.modeA') },
                      { value: 'B', label: t('wizard.modeB') },
                    ]}
                  />
                )}
              </LabeledField>
              <LabeledField form={form} name="projectType" label={t('wizard.typeLabel')}>
                {(p) => (
                  <SelectInput
                    label={t('wizard.typeLabel')}
                    value={p.value}
                    onChange={p.onChange}
                    error={p.error}
                    options={[
                      { value: 'GREENFIELD', label: t('wizard.typeGreenfield') },
                      { value: 'BROWNFIELD', label: t('wizard.typeBrownfield') },
                    ]}
                  />
                )}
              </LabeledField>
              <LabeledField form={form} name="versionConstraints" label={t('wizard.versionConstraintsLabel')}>
                {(p) => <TextInput label={t('wizard.versionConstraintsLabel')} value={p.value} onChange={p.onChange} error={p.error} />}
              </LabeledField>
            </>
          )}

          {step === 3 && (
            <div className="stack">
              <LabeledField form={form} name="defaultLanguage" label={t('wizard.defaultLanguageLabel')}>
                {(p) => (
                  <SelectInput
                    label={t('wizard.defaultLanguageLabel')}
                    value={p.value}
                    onChange={p.onChange}
                    error={p.error}
                    options={[
                      { value: 'zh-CN', label: '简体中文' },
                      { value: 'en', label: 'English' },
                    ]}
                  />
                )}
              </LabeledField>
              {/* UI-PRJ-002 lists translation languages among the wizard's fields, and S1 stores the
                  list it is sent (`services/project.py` hands `[default, ...translations]` to the
                  Blueprint). Until now the value existed in the form and was never collectable. */}
              <div>
                <label className="muted" style={{ fontSize: 12 }}>{t('wizard.translationLanguagesLabel')}</label>
                <div className="row">
                  {(['zh-CN', 'en'] as const).map((code) => {
                    const chosen = form.watch('translationLanguages').includes(code);
                    const isDefault = form.watch('defaultLanguage') === code;
                    return (
                      <CheckboxInput
                        key={code}
                        label={code === 'zh-CN' ? '简体中文' : 'English'}
                        checked={chosen || isDefault}
                        disabled={isDefault}
                        onChange={() =>
                          form.setValue(
                            'translationLanguages',
                            chosen
                              ? form.getValues('translationLanguages').filter((x) => x !== code)
                              : [...form.getValues('translationLanguages'), code],
                            { shouldValidate: true },
                          )
                        }
                      />
                    );
                  })}
                </div>
                <p className="muted" style={{ fontSize: 12 }}>{t('wizard.translationHelp')}</p>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="stack">
              <Heading level="5" size="small">{t('wizard.targetsLabel')}</Heading>
              {noEnvironments ? (
                <EmptyState action={<Link to="/environments">{t('wizard.goToEnvironments')}</Link>}>
                  {t('wizard.noEnvironments')}
                </EmptyState>
              ) : null}
              {environmentOptions.map((opt) => (
                <div key={opt.value} className="row" style={{ justifyContent: 'space-between' }}>
                  <span>{opt.label}</span>
                  {targets.some((x) => x.environmentKey === opt.value) ? (
                    <span className="row">
                      <CheckboxInput label={t('wizard.designReferenceLabel')} checked={targets.find((x) => x.environmentKey === opt.value)?.isDesignReference ?? false} onChange={() => toggleDesignRef(opt.value)} />
                      <Button secondary small onClick={() => moveTarget(targets.findIndex((x) => x.environmentKey === opt.value), -1)}>{t('wizard.moveUp')}</Button>
                      <Button secondary small onClick={() => moveTarget(targets.findIndex((x) => x.environmentKey === opt.value), 1)}>{t('wizard.moveDown')}</Button>
                    </span>
                  ) : (
                    <Button secondary onClick={() => addEnvironment(opt.value)}>{t('wizard.addEnvironment')}</Button>
                  )}
                </div>
              ))}
              {/* The reason the step will not advance, in the step. An error raised and rendered
                  nowhere is how this button came to look broken rather than blocked. */}
              {targetsError ? (
                <p className="muted" role="alert" style={{ color: 'var(--color-error)' }}>
                  {targetsError}
                </p>
              ) : null}
            </div>
          )}

          {step === 5 && (
            <ReviewSummary values={form.getValues()} t={t} />
          )}
        </div>

        {currentFields.length === 0 && step === 5 ? (
          <div className="row" style={{ marginTop: 20 }}>
            {/* UI-BR-01: `project.create` is S1's answer for this button (platform scope), so an
                account without the right sees it disabled with S1's reason instead of a 403 after
                filling in six steps. */}
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.projectCreate}
              onExecute={submit}
              loading={create.isPending}
              disabled={!hasOperator()}
            >
              {t('wizard.submit')}
            </AllowedActionButton>
            {!hasOperator() ? <span className="muted">{t('operator.missing')}</span> : null}
          </div>
        ) : (
          <div className="row" style={{ marginTop: 20, justifyContent: 'space-between' }}>
            <Button secondary disabled={step === 0} onClick={() => setStep((s) => s - 1)}>{t('common.previous')}</Button>
            <Button primary onClick={goNext}>{step === STEPS.length - 2 ? t('common.finish') : t('common.next')}</Button>
          </div>
        )}
      </div>
        </div>
      </div>
    </PageContainer>
  );
}

/** Minimal RHF text/select adapter to keep the wizard readable. */
function LabeledField({
  form,
  name,
  children,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  form: any;
  name: keyof WizardValues;
  label: string;
  required?: boolean;
  children: (props: { value: string; onChange: (v: string) => void; error?: string }) => React.ReactElement;
}) {
  const value = form.watch(name) ?? '';
  const error = form.formState.errors[name]?.message as string | undefined;
  const field = typeof value === 'string' ? value : Array.isArray(value) ? value.join(', ') : String(value);
  // The control inside already renders the label (with `for`), so an outer label here would repeat
  // the same words on screen and leave one of them unassociated.
  return <div>{children({ value: field, onChange: (v) => form.setValue(name, v, { shouldValidate: true }), error })}</div>;
}

function ReviewSummary({ values, t }: { values: WizardValues; t: (k: string) => string }) {
  const rows: Array<[string, string]> = [
    [t('wizard.basicsNameLabel'), values.name],
    [t('wizard.ownerNameLabel'), values.ownerName],
    [t('wizard.modeLabel'), deliveryModeLabel(values.deliveryMode)],
    [t('wizard.typeLabel'), projectTypeLabel(values.projectType)],
    [t('wizard.defaultLanguageLabel'), values.defaultLanguage],
    [t('wizard.targetsLabel'), values.targets.map((x) => `${x.environmentKey}${x.isDesignReference ? ' ★' : ''}`).join(', ')],
  ];
  return (
    <div className="stack">
      <Heading level="5" size="small">{t('wizard.summaryTitle')}</Heading>
      <table>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <th style={{ textAlign: 'left', padding: '4px 12px 4px 0' }} className="muted">{k}</th>
              <td>{v || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
