/*
 * UI-HELP-001: a worked example, not another glossary.
 *
 * The generic help drawer answers "what is a stage"; this page answers "what do I press next,
 * and what will the AI actually produce". It follows the tq1 material (出院随访.txt) through the
 * same buttons an operator uses, and states which results are still candidates.
 */
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Button, Tag } from '@dhis2/ui';
import { PageContainer } from '@/lib/PageContainer';
import { SectionCard } from '@/components/SectionCard';

const STEPS = [
  'prepare',
  'project',
  'upload',
  'parse',
  'review',
  'questions',
  'requirements',
  'design',
  'delivery',
  'verify',
] as const;

const OUTPUTS = [
  'requirements',
  'domain',
  'discovery',
  'mapping',
  'page',
  'build',
  'execution',
] as const;

const MISTAKES = ['needsReview', 'mapping', 'candidate', 'nothing'] as const;

export function TutorialView() {
  const { t } = useTranslation();

  return (
    <PageContainer
      title={t('tutorial.title')}
      description={t('tutorial.intro')}
      helpKey="tutorial"
      actions={
        <>
          <Link to="/projects">
            <Button secondary small>{t('tutorial.links.projects')}</Button>
          </Link>
          <Link to="/getting-started">
            <Button secondary small>{t('tutorial.links.setup')}</Button>
          </Link>
        </>
      }
    >
      <div className="stack" style={{ gap: 16 }}>
        <SectionCard title={t('tutorial.exampleTitle')}>
          <div className="stack" style={{ gap: 10 }}>
            <p style={{ margin: 0 }}>{t('tutorial.exampleBody')}</p>
            <div>
              <Tag>{t('tutorial.exampleResult')}</Tag>
            </div>
          </div>
        </SectionCard>

        <SectionCard title={t('tutorial.stepsTitle')}>
          <ol className="tutorial-steps">
            {STEPS.map((step, index) => (
              <li key={step}>
                <span className="tutorial-steps__index" aria-hidden="true">{index + 1}</span>
                <div className="tutorial-steps__copy">
                  <h3>{t(`tutorial.steps.${step}.title`)}</h3>
                  <p className="tutorial-steps__where">{t(`tutorial.steps.${step}.where`)}</p>
                  <p>{t(`tutorial.steps.${step}.body`)}</p>
                  <p className="tutorial-steps__result">
                    <strong>{t('tutorial.resultLabel')}</strong> {t(`tutorial.steps.${step}.result`)}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </SectionCard>

        <SectionCard title={t('tutorial.outputsTitle')} description={t('tutorial.outputsIntro')}>
          <div className="tutorial-outputs">
            {OUTPUTS.map((output) => (
              <div key={output} className="tutorial-output">
                <strong>{t(`tutorial.outputs.${output}.title`)}</strong>
                <p>{t(`tutorial.outputs.${output}.body`)}</p>
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard title={t('tutorial.mistakesTitle')}>
          <div className="tutorial-mistakes">
            {MISTAKES.map((mistake) => (
              <div key={mistake} className="tutorial-mistake">
                <strong>{t(`tutorial.mistakes.${mistake}.title`)}</strong>
                <p>{t(`tutorial.mistakes.${mistake}.body`)}</p>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    </PageContainer>
  );
}
