/*
 * UI-REV-005: the validation issue list, filtered by level / source stage / rule / object, with a
 * jump to the object and to the stage that produced it, and an explanation request routed through
 * S1 to S3 (AI-AG-014).
 *
 * The filters are query parameters on S1's endpoint. Filtering the returned page instead would
 * silently answer "no issues" for every issue that fell outside the first page, which is the
 * opposite of what a validation report is for.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { DataTable, DataTableBody, DataTableCell, DataTableHead, DataTableRow, Tag } from '@dhis2/ui';
import { JsonEditor } from '@/components/JsonEditor';
import { SelectInput, TextInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { Loading } from '@/lib/PageContainer';
import { ACTION } from '@/api/actions';
import { artifactContentOptions, stagesOptions, validationIssuesOptions } from '@/api/queries';
import { useExplainArtifact } from '@/api/mutations';
import type { IssueView } from '@/api/types';
import { severityLabel, stageTypeLabel } from '@/lib/labels';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';

const LEVELS = ['BLOCKING', 'ERROR', 'WARNING', 'INFO'];

export function ValidationIssues() {
  const { t } = useTranslation();
  const { projectId, workflowId } = useOutletContext<WorkspaceContext>();
  const [level, setLevel] = useState('');
  const [sourceStage, setSourceStage] = useState('');
  const [ruleId, setRuleId] = useState('');
  const [businessKey, setBusinessKey] = useState('');
  const issues = useQuery(
    validationIssuesOptions(workflowId, { level: level || undefined, sourceStage: sourceStage || undefined, ruleId: ruleId || undefined, businessKey: businessKey || undefined }),
  );
  const stages = useQuery(stagesOptions(workflowId));
  const rows = issues.data?.items ?? [];
  const stageIdFor = (stageType: string | null | undefined) =>
    (stages.data?.items ?? []).find((s) => s.stageType === stageType)?.id;

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>{t('review.issuesTitle')}</h2>
          <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>{t('page.issues')}</p>
        </div>
        <span className="chip">{t('common.rowCount', { count: rows.length })}</span>
      </div>
      <div className="filter-bar">
        <div style={{ minWidth: 180 }}>
          <SelectInput
            label={t('review.filterLevel')}
            value={level}
            onChange={setLevel}
            options={[{ value: '', label: t('common.all') }, ...LEVELS.map((name) => ({ value: name, label: severityLabel(name) }))]}
          />
        </div>
        <TextInput label={t('review.filterStage')} value={sourceStage} onChange={setSourceStage} />
        <TextInput label={t('review.filterRule')} value={ruleId} onChange={setRuleId} />
        <TextInput label={t('review.filterObject')} value={businessKey} onChange={setBusinessKey} />
      </div>
      {issues.isLoading ? <Loading /> : null}
      {!issues.isLoading && !rows.length ? <p className="muted">{t('review.issuesEmpty')}</p> : null}
      {rows.length ? (
        <DataTable>
          <DataTableHead>
            <DataTableRow>
              <DataTableCell tag="th">{t('common.status')}</DataTableCell>
              <DataTableCell tag="th">{t('review.issueObject')}</DataTableCell>
              <DataTableCell tag="th">{t('review.issueDescription')}</DataTableCell>
              <DataTableCell tag="th">{t('review.filterStage')}</DataTableCell>
              <DataTableCell tag="th">{t('common.actions')}</DataTableCell>
            </DataTableRow>
          </DataTableHead>
          <DataTableBody>
            {rows.map((issue) => (
              <IssueRow key={issue.issueId} issue={issue} projectId={projectId} workflowId={workflowId} stageId={stageIdFor(issue.sourceStage)} />
            ))}
          </DataTableBody>
        </DataTable>
      ) : null}
    </div>
  );
}

function IssueRow({
  issue,
  projectId,
  workflowId,
  stageId,
}: {
  issue: IssueView;
  projectId: string;
  workflowId?: string;
  stageId?: string;
}) {
  const { t } = useTranslation();
  const explain = useExplainArtifact(projectId);
  const [explained, setExplained] = useState<string | null>(null);
  const content = useQuery(artifactContentOptions(explained ?? undefined));

  return (
    <DataTableRow>
      <DataTableCell>
        <Tag>{issue.level ? severityLabel(issue.level) : '—'}</Tag>
        <div className="muted mono" style={{ fontSize: 11 }}>{issue.ruleId ?? ''}</div>
      </DataTableCell>
      <DataTableCell>
        {/* Jump to the object. This pointed at `/registry`, a route that does not exist, so every
            jump landed on the 404 page; the metadata view is where a business key is shown and it
            reads the focus as `object`. */}
        <Link to={`/projects/${projectId}/metadata?object=${encodeURIComponent(issue.businessKey ?? issue.objectKey ?? '')}`} className="mono">
          {issue.businessKey ?? issue.objectKey ?? '—'}
        </Link>
      </DataTableCell>
      <DataTableCell>
        <div>{issue.description}</div>
        {issue.remediation ? <div className="muted" style={{ fontSize: 12 }}>{issue.remediation}</div> : null}
      </DataTableCell>
      <DataTableCell>
        {stageId ? (
          <Link to={`/projects/${projectId}/stages/${stageId}`}>{stageTypeLabel(issue.sourceStage) || '—'}</Link>
        ) : (
          <span>{stageTypeLabel(issue.sourceStage) || '—'}</span>
        )}
      </DataTableCell>
      <DataTableCell>
        <div className="stack" style={{ gap: 4 }}>
          <AllowedActionButton
            workflowId={workflowId}
            action={ACTION.artifactExplain}
            disabled={!issue.artifactId}
            loading={explain.isPending}
            onExecute={async () => {
              if (!issue.artifactId) return;
              const answer = await explain.mutateAsync({ artifactId: issue.artifactId });
              setExplained(answer.explanationArtifactId ?? null);
            }}
          >
            {t('review.explain')}
          </AllowedActionButton>
          {explained === null && explain.error ? <span className="muted" style={{ fontSize: 11 }}>{t('review.explainUnavailable')}</span> : null}
          {content.data ? (
            <JsonEditor value={content.data} readOnly height="200px" />
          ) : null}
        </div>
      </DataTableCell>
    </DataTableRow>
  );
}
