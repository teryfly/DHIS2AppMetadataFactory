/*
 * UI-REQS-003: review the column mapping S3 proposed for an Excel/CSV input, edit it, confirm it
 * and re-parse with the confirmation (S5IF-INP04 — the same parse endpoint carries the mapping).
 *
 * Each proposal is one sheet's review Question: the headers S3 read, the mapping it suggests and
 * the requirement entries the answer affects. Editing here changes what the next parse sees; it
 * does not touch the uploaded file, which stays the same hash (FR-REQ-001).
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, DataTable, DataTableBody, DataTableCell, DataTableHead, DataTableRow } from '@dhis2/ui';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { TextInput } from '@/components/form';
import { NoticeBar } from '@/components/NoticeBar';
import { Loading } from '@/lib/PageContainer';
import { ACTION } from '@/api/actions';
import { actionIntent } from '@/api/idempotency';
import { columnMappingOptions } from '@/api/queries';
import { useParseInput } from '@/api/mutations';
import type { ColumnMappingProposalView } from '@/api/types';

export function ColumnMappingView() {
  const { t } = useTranslation();
  const { projectId = '', artifactId = '' } = useParams();
  const mapping = useQuery(columnMappingOptions(projectId, artifactId));

  if (mapping.isLoading) return <Loading />;
  const proposals = mapping.data?.proposals ?? [];

  return (
    <div className="stack">
      <h3 style={{ margin: 0 }}>{t('inputs.mappingTitle')}</h3>
      {mapping.isError ? <NoticeBar tone="error">{t('inputs.mappingLoadFailed')}</NoticeBar> : null}
      {!proposals.length ? (
        <div className="stack" style={{ gap: 8 }}>
          <p className="muted" style={{ margin: 0 }}>{t('inputs.mappingEmpty')}</p>
          <p className="muted" style={{ margin: 0 }}>{t('inputs.mappingOnlyTabular')}</p>
          <div>
            <Link to={`/projects/${projectId}/inputs`}>
              <Button secondary small>{t('inputs.mappingReviewParsed')}</Button>
            </Link>
          </div>
        </div>
      ) : null}
      {proposals.map((proposal, index) => (
        <ProposalCard
          key={`${proposal.questionId ?? 'sheet'}-${index}`}
          projectId={projectId}
          workflowId={mapping.data?.workflowId}
          artifactId={artifactId}
          proposal={proposal}
        />
      ))}
    </div>
  );
}

function ProposalCard({
  projectId,
  workflowId,
  artifactId,
  proposal,
}: {
  projectId: string;
  workflowId: string | null | undefined;
  artifactId: string;
  proposal: ColumnMappingProposalView;
}) {
  const { t } = useTranslation();
  const parse = useParseInput(projectId, workflowId ?? '');
  const initial = proposal.columnMapping ?? {};
  const rows = proposal.headers ?? Object.keys(initial);
  const [mapping, setMapping] = useState<Record<string, string>>(initial);

  const confirm = () =>
    parse.mutateAsync({
      artifactId,
      options: { columnMapping: compact(mapping), ...(proposal.sheet ? { sheet: proposal.sheet } : {}) },
      // UI-REQ-001: confirming a *different* mapping is a new action, confirming the same one
      // twice must replay the first. The digest is what makes the intent say that.
      intent: actionIntent('POST', 'input.confirm_column_mapping', artifactId, digest(mapping)),
    });

  return (
    <div className="stack" style={{ gap: 8 }}>
      <strong>{proposal.sheet ? `${t('inputs.sheet')}: ${proposal.sheet}` : t('inputs.mappingUntitled')}</strong>
      {proposal.question ? <p className="muted" style={{ fontSize: 12 }}>{proposal.question}</p> : null}
      <DataTable>
        <DataTableHead>
          <DataTableRow>
            <DataTableCell tag="th">{t('inputs.sourceColumn')}</DataTableCell>
            <DataTableCell tag="th">{t('inputs.targetField')}</DataTableCell>
          </DataTableRow>
        </DataTableHead>
        <DataTableBody>
          {rows.map((header) => (
            <DataTableRow key={header}>
              <DataTableCell className="mono">{header}</DataTableCell>
              <DataTableCell>
                <TextInput
                  label={t('inputs.targetField')}
                  value={mapping[header] ?? ''}
                  onChange={(value) => setMapping({ ...mapping, [header]: value })}
                />
              </DataTableCell>
            </DataTableRow>
          ))}
        </DataTableBody>
      </DataTable>
      <div className="row" style={{ gap: 8 }}>
        {/* Only the target is editable: the source columns are what the file says, and a row
            invented here would confirm a mapping to a column the parser cannot read. */}
        <AllowedActionButton
          workflowId={workflowId ?? undefined}
          action={ACTION.inputConfirmColumnMapping}
          loading={parse.isPending}
          onExecute={confirm}
        >
          {t('inputs.confirmAndParse')}
        </AllowedActionButton>
      </div>
      {proposal.affectedKeys?.length ? (
        <p className="muted" style={{ fontSize: 12 }}>
          {t('inputs.affects', { keys: proposal.affectedKeys.join(', ') })}
        </p>
      ) : null}
    </div>
  );
}

function compact(value: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(value).filter(([, target]) => target.trim() !== ''));
}

/** Order-independent 32-bit digest of a mapping, used only to key an idempotency intent. */
function digest(value: Record<string, string>): string {
  const text = Object.entries(value)
    .filter(([, target]) => target.trim() !== '')
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([source, target]) => `${source}=${target}`)
    .join(';');
  let hash = 5381;
  for (const char of text) hash = ((hash << 5) + hash + char.charCodeAt(0)) | 0;
  return (hash >>> 0).toString(36);
}
