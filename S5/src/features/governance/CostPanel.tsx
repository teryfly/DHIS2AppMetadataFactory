/*
 * UI-ADM-005 (AI-COST-001~003, S5IF-GOV18): the cost half of Governance — the usage rollup and
 * the budget switch that was delivered with M4.
 *
 * Two rules shape this file.  The rollup's grouping dimensions are read from the answer
 * (`dimensions`), never listed here, because which dimensions exist is S3's decision.  And the
 * budget's `state` is S3's verdict, displayed as it arrives: a screen that compared `spent` to
 * `limit` itself would disagree with the rule that refuses a run (AI-COST-003), which is exactly
 * the kind of second source of truth UI-APP-006 forbids.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { NoticeBar } from '@/components/NoticeBar';
import { TextInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { projectsListOptions, governanceUsageOptions, governanceBudgetOptions } from '@/api/queries';
import { useSetBudget } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import type { BudgetView, UsageItemView } from '@/api/types';
import { budgetStateLabel, usageDimensionLabel, usageValueLabel } from '@/lib/labels';

function stateLabel(t: ReturnType<typeof useTranslation>['t'], budget: BudgetView): string {
  switch (budget.state) {
    case 'WARNING':
      return t('governance.stateWarning', { value: budget.thresholdReached ?? '' });
    case 'EXCEEDED':
      return t('governance.stateExceeded');
    case 'OK':
      return t('governance.stateOk');
    default:
      return t('governance.stateUnset');
  }
}

export function CostPanel() {
  const { t } = useTranslation();
  const projects = useQuery(projectsListOptions());
  const projectIds = (projects.data?.items ?? []).map((item) => item.id ?? '');
  const [projectId, setProjectId] = useState('');
  const active = projectId || projectIds[0] || '';
  const [groupBy, setGroupBy] = useState<string[]>(['stage', 'agent', 'model']);
  const usage = useQuery(governanceUsageOptions(active, groupBy));
  const budget = useQuery(governanceBudgetOptions(active));
  const setBudget = useSetBudget(active);
  const [limit, setLimit] = useState('');
  const [thresholds, setThresholds] = useState('0.8');
  const [currency, setCurrency] = useState('USD');

  const dimensions = ((usage.data as { dimensions?: string[] })?.dimensions ?? []).filter(Boolean);
  const items = ((usage.data as { items?: UsageItemView[] })?.items ?? []) as UsageItemView[];
  const view = (budget.data ?? {}) as BudgetView;
  const failure = setBudget.error;

  const toggle = (dimension: string) =>
    setGroupBy((current) =>
      current.includes(dimension) ? current.filter((item) => item !== dimension) : [...current, dimension],
    );

  const write = () =>
    setBudget.mutate({
      limit: limit.trim() === '' ? null : Number(limit),
      thresholds: thresholds
        .split(',')
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isFinite(item)),
      currency: currency.trim() || 'USD',
    });

  return (
    <div className="row" style={{ gap: 24, alignItems: 'flex-start' }}>
      <Card>
        <Heading level={5} size="small">{t('governance.usage')}</Heading>
        <div className="stack" style={{ gap: 8 }}>
          <TextInput
            label={t('governance.usageProject')}
            value={active}
            onChange={setProjectId}
            helpText={projectIds.length ? `${projectIds.length}` : undefined}
          />
          <div className="row" style={{ gap: 10, flexWrap: 'wrap' }} role="group" aria-label={t('governance.usageGroup')}>
            {dimensions.map((dimension) => (
              <label key={dimension} className="row" style={{ gap: 4 }}>
                <input
                  type="checkbox"
                  checked={groupBy.includes(dimension)}
                  onChange={() => toggle(dimension)}
                  aria-label={dimension}
                />
                <span className="mono">{usageDimensionLabel(dimension)}</span>
              </label>
            ))}
          </div>
          {items.length === 0 ? (
            <p className="muted">{t('governance.noUsage')}</p>
          ) : (
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  {groupBy.map((dimension) => (
                    <DataTableCell key={dimension} tag="th">{usageDimensionLabel(dimension)}</DataTableCell>
                  ))}
                  <DataTableCell tag="th">{t('governance.usageCalls')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.usageFailed')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.usageInputTokens')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.usageOutputTokens')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.usageCost')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {items.map((row, index) => (
                  <DataTableRow key={index}>
                    {groupBy.map((dimension) => (
                      <DataTableCell key={dimension}>
                        {usageValueLabel(dimension, String((row as Record<string, unknown>)[dimension] ?? '—'))}
                      </DataTableCell>
                    ))}
                    <DataTableCell>{row.calls ?? 0}</DataTableCell>
                    <DataTableCell>{row.failedCalls ?? 0}</DataTableCell>
                    <DataTableCell>{row.inputTokens ?? 0}</DataTableCell>
                    <DataTableCell>{row.outputTokens ?? 0}</DataTableCell>
                    <DataTableCell>{row.estimatedCost ?? 0}</DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
          )}
        </div>
      </Card>

      <Card>
        <Heading level={5} size="small">{t('governance.budget')}</Heading>
        <div className="stack" style={{ gap: 8 }}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <span>{t('governance.budgetSpent')}: {view.spent ?? 0}</span>
            <Tag>{budgetStateLabel(view.state ?? 'UNSET')}</Tag>
            {/* the sentence belongs to the banner below, not to this line: repeating it here would
                read as two verdicts when there is one */}
            <span className="muted">
              {t('governance.budgetSpent')}: {view.spent ?? 0} / {view.limit ?? '—'} {view.currency ?? ''}
            </span>
          </div>

          {/* CP-WF-030 / UI-WB-009: the refusal a run would get is stated in the same words S1 uses */}
          {view.state === 'EXCEEDED' ? <NoticeBar tone="error">{t('governance.stateExceeded')}</NoticeBar> : null}
          {view.state === 'WARNING' ? <NoticeBar tone="warning">{stateLabel(t, view)}</NoticeBar> : null}

          {failure && isApiError(failure) ? (
            <NoticeBar tone="error" title={t('error.title')}>
              {translateErrorCode(failure.errorCode, failure.message)}
            </NoticeBar>
          ) : null}

          <TextInput label={t('governance.budgetLimit')} value={limit} onChange={setLimit} type="number" />
          <TextInput label={t('governance.budgetThresholds')} value={thresholds} onChange={setThresholds} />
          <TextInput label={t('governance.budgetCurrency')} value={currency} onChange={setCurrency} />

          <div className="row" style={{ gap: 8 }}>
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governanceBudgetSet}
              onExecute={write}
              loading={setBudget.isPending}
            >
              {t('governance.budgetSave')}
            </AllowedActionButton>
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governanceBudgetSet}
              tone="secondary"
              onExecute={() => setBudget.mutate({ limit: null, thresholds: [], currency: currency.trim() || 'USD' })}
              loading={setBudget.isPending}
            >
              {t('governance.budgetClear')}
            </AllowedActionButton>
          </div>
          {setBudget.isSuccess ? <p className="muted">{t('governance.budgetSaved')}</p> : null}
        </div>
      </Card>
    </div>
  );
}
