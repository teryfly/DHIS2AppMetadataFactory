/*
 * UI-ADM-003: prompt versions and what makes one effective.
 *
 * This panel replaces a sentence that said the feature was coming. The rule it enforces is
 * UI-ADM-003's — a version with no evaluation or no Golden Project regression result may not be
 * activated — and both halves of that precondition are now readable from S1
 * (`GET /governance/evaluations`, `GET /governance/regression-records`), so the reason appears
 * before the attempt instead of after it.
 */
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { Loading } from '@/lib/PageContainer';
import { NoticeBar } from '@/components/NoticeBar';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { EmptyState } from '@/components/EmptyState';
import { ACTION } from '@/api/actions';
import {
  governanceEvaluationsOptions,
  governanceModelsOptions,
  governancePromptsOptions,
  governanceRegressionsOptions,
} from '@/api/queries';
import { useActivatePrompt } from '@/api/mutations';
import { pushToast } from '@/components/toast';

type Row = Record<string, unknown>;

/** S1 names a prompt version `name.vN` in regression records; evaluations quote it as a free
 *  string. Accept either spelling rather than betting on one — the alternative is a gate that
 *  silently reads "no evaluation exists". */
function evaluationMatches(items: Row[], name: string, version: string | number): boolean {
  const dotted = `${name}.v${version}`;
  const at = `${name}@${version}`;
  return items.some((e) => {
    const prompt = String(e.prompt ?? '');
    // A failed evaluation is not the evidence the gate asks for; showing it as coverage would let a
    // version be activated on a result that says the opposite.
    return (e.passed === undefined || e.passed === true) && (prompt.includes(dotted) || prompt.includes(at));
  });
}

function regressionMatches(items: Row[], name: string, version: string | number): boolean {
  const dotted = `${name}.v${version}`;
  return items.some((r) => r.passed !== false && String(r.promptVersionId ?? '') === dotted);
}

export function PromptPanel() {
  const { t } = useTranslation();
  const prompts = useQuery(governancePromptsOptions());
  const evaluations = useQuery(governanceEvaluationsOptions());
  const regressions = useQuery(governanceRegressionsOptions());
  const models = useQuery(governanceModelsOptions());
  const activate = useActivatePrompt();

  if (prompts.isLoading) return <Loading />;

  const rows = (prompts.data?.items ?? []) as Row[];
  const evaluationRows = (evaluations.data?.items ?? []) as Row[];
  const regressionRows = (regressions.data?.items ?? []) as Row[];
  const modelRows = (models.data?.items ?? []) as Row[];

  return (
    <Card>
      <div className="stack" style={{ gap: 8 }}>
        <Heading level={5} size="small">{t('governance.prompts')}</Heading>
        {prompts.isError || evaluations.isError || regressions.isError ? (
          <NoticeBar tone="error" title={t('error.title')}>
            {t('governance.promptFactsMissing')}
          </NoticeBar>
        ) : null}
        {rows.length === 0 ? (
          <EmptyState>
            <p>{t('governance.noPrompts')}</p>
            <p className="muted">{t('governance.noPromptsBody')}</p>
          </EmptyState>
        ) : null}
        {rows.map((p) => {
          const name = String(p.name ?? '');
          const version = String(p.version ?? '');
          const agentType = String(p.agentType ?? '');
          const active = Boolean(p.active);
          const hasEvaluation = evaluationMatches(evaluationRows, name, version);
          const hasRegression = regressionMatches(regressionRows, name, version);
          // `applicableAgents` is a map of agent -> priority, not a list (S5IF-GOV05).
          const applicable = (m: Row) => {
            const raw = m.applicableAgents;
            if (Array.isArray(raw)) return raw.map(String).includes(agentType);
            if (raw && typeof raw === 'object') return Object.keys(raw as Record<string, unknown>).includes(agentType);
            return false;
          };
          const availableModels = modelRows
            .filter((m) => m.enabled && applicable(m))
            .map((m) => String(m.ref ?? m.id ?? ''))
            .filter(Boolean);
          const missing = [
            ...(!hasEvaluation ? [t('governance.needEvaluation')] : []),
            ...(!hasRegression ? [t('governance.needRegression')] : []),
            ...(availableModels.length === 0 ? [t('governance.needModel')] : []),
          ];
          const ready = missing.length === 0;
          return (
            <div key={String(p.qualifiedName ?? `${name}.${version}`)} className="stack" style={{ gap: 2 }}>
              <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
                <strong>{name}</strong>
                <span className="muted">v{version}</span>
                <span className="muted mono">{agentType}</span>
                {active ? <span className="muted">{t('governance.active')}</span> : null}
              </div>
              <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                <AllowedActionButton
                  workflowId={undefined}
                  scope="platform"
                  action={ACTION.governancePromptActivate}
                  tone={active ? 'secondary' : 'primary'}
                  disabled={active || !ready}
                  loading={activate.isPending}
                  onExecute={() =>
                    activate.mutate(
                      { agentType, promptName: name, promptVersion: Number(version), modelList: availableModels } as never,
                      {
                        onSuccess: () => pushToast({ tone: 'success', title: t('governance.promptActivated', { name, version }) }),
                      },
                    )
                  }
                >
                  {t('governance.activate')}
                </AllowedActionButton>
                {!ready && !active ? <span className="muted">{missing.join(' · ')}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
