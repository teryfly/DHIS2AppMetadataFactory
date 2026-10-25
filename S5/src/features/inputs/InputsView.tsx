/*
 * UI-REQS-001 / UI-REQS-002: the project's business inputs — what was uploaded, at which
 * declared classification, and how far its parsing got.
 *
 * Two rules shape this view. The classification on a row is the uploader's declaration, recorded
 * verbatim by S1 (FR-REQ-001): nothing here infers it from the filename, because a PHI/PII file
 * that reads as INTERNAL would pass every policy check that keys on PHI_PII. And the parse
 * status is S1's projection of the parse records (S5IF-INP02) — an input that was never parsed
 * has no status, which is a state of its own and not a failure.
 */
import { useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { Link, useOutletContext } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, LinearLoader, Tag } from '@dhis2/ui';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { CheckboxInput, SelectInput } from '@/components/form';
import { SectionCard } from '@/components/SectionCard';
import { EmptyState } from '@/components/EmptyState';
import { Loading } from '@/lib/PageContainer';
import { ACTION } from '@/api/actions';
import { actionIntent } from '@/api/idempotency';
import { isApiError, type ApiError } from '@/api/errors';
import { inputsOptions, stagesOptions, uploadLimitsOptions } from '@/api/queries';
import { useParseInput, useUploadInput } from '@/api/mutations';
import { DATA_CLASSIFICATIONS, type DataClassificationName } from '@/api/upload';
import { formatRelativeTime } from '@/lib/time';
import { formatBytes } from '@/lib/format';
import type { InputView } from '@/api/types';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';
import { classificationLabel } from '@/lib/labels';
import { NrrCard } from '@/features/workspace/NrrCard';

export function InputsView() {
  const { t } = useTranslation();
  const { projectId, workflowId } = useOutletContext<WorkspaceContext>();
  const inputs = useQuery({
    ...inputsOptions(projectId),
    // While a parse is claimed, the status is the only thing that changes without an event
    // of its own; the SSE `input.parse` terminal event stops this poll early.
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((item) => item.parseStatus === 'PARSING') ? 3_000 : false,
  });
  const stages = useQuery(stagesOptions(workflowId));
  const requirementsStageId = (stages.data?.items ?? []).find(
    (stage) => stage.stageType === 'REQUIREMENTS',
  )?.id;
  const rows = inputs.data?.items ?? [];

  if (inputs.isLoading) return <Loading />;

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>{t('nav.inputs')}</h2>
          <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>{t('page.inputs')}</p>
        </div>
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <Link to="/help/tutorial">
            <Button secondary small>{t('inputs.exampleLink')}</Button>
          </Link>
          <span className="chip">{t('common.rowCount', { count: rows.length })}</span>
        </div>
      </div>

      <UploadPanel projectId={projectId} workflowId={workflowId} />

      <SectionCard title={t('inputs.title')} description={t('page.inputs')}>
        {rows.length === 0 ? (
          <EmptyState icon="file" title={t('inputs.empty')}>
            {t('inputs.uploadLimits', { max: '25 MB', types: t('inputs.anyType') })}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="record-table">
              <thead>
                <tr>
                  <th>{t('inputs.filename')}</th>
                  <th>{t('inputs.classification')}</th>
                  <th>{t('inputs.parseStatus')}</th>
                  <th>{t('inputs.uploadedAt')}</th>
                  <th>{t('common.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <InputRow
                    key={row.artifactId}
                    row={row}
                    projectId={projectId}
                    workflowId={workflowId}
                    requirementsStageId={requirementsStageId}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* UI-REQS-004: the parsed requirements belong where the upload is, not only behind a
          stage route. A model-read text document shows nothing here until this card exists. */}
      {workflowId ? (
        <NrrCard
          workflowId={workflowId}
          projectId={projectId}
          stageId={requirementsStageId}
        />
      ) : null}
    </div>
  );
}

/** Only a tabular parse has columns, so only it has a mapping to review (AI-INP-006). */
function isTabular(parserType?: string | null): boolean {
  return parserType === 'CSV' || parserType === 'XLSX';
}

function InputRow({
  row,
  projectId,
  workflowId,
  requirementsStageId,
}: {
  row: InputView;
  projectId: string;
  workflowId?: string;
  requirementsStageId?: string;
}) {
  const { t } = useTranslation();
  const parse = useParseInput(projectId, workflowId ?? '');
  const status = row.parseStatus;

  return (
    <tr>
      <td>
        {/* The name is how an operator recognises their own upload, so it is the thing they click to
            read the bytes back. The artifact record carries no filename (artifact_view exposes type,
            media type, size and hash), which is why the review page opens on the id. */}
        <strong>
          <Link
            to={`/projects/${projectId}/artifacts/${encodeURIComponent(row.artifactId)}${
              row.filename ? `?name=${encodeURIComponent(row.filename)}` : ''
            }`}
          >
            {row.filename ?? row.artifactId}
          </Link>
        </strong>
        <div className="muted" style={{ fontSize: 12 }}>
          {row.mediaType ?? '—'} · {formatBytes(row.sizeBytes)}
        </div>
      </td>
      <td>
        <Tag>{classificationLabel(row.dataClassification) || '—'}</Tag>
      </td>
      <td>
        <div className="stack" style={{ gap: 4 }}>
          {row.parseRunId ? (
            <Link to={`/projects/${projectId}/inputs/${row.artifactId}/llm-log`}>
              <Tag>{status ? t(`inputs.status.${status}`, { defaultValue: status }) : t('inputs.status.NONE')}</Tag>
            </Link>
          ) : (
            <Tag>{status ? t(`inputs.status.${status}`, { defaultValue: status }) : t('inputs.status.NONE')}</Tag>
          )}
          {/* UI-REQS-002: the reason sits next to the status, not only in a toast that expired. */}
          {row.errorCode ? <span className="muted" style={{ fontSize: 12 }}>{explainError(t, row)}</span> : null}
        </div>
      </td>
      <td>{row.createdAt ? formatRelativeTime(row.createdAt, t) : '—'}</td>
      <td>
        <div className="table-actions">
          {/* No targetId: S1's forwarding actions carry no target on purpose (the id of an input
              is not knowable to the workflow-level call), so the key this button reads is the
              bare action name. */}
          <AllowedActionButton
            workflowId={workflowId}
            action={ACTION.inputParse}
            loading={parse.isPending || status === 'PARSING'}
            disabled={status === 'PARSING'}
            onExecute={() =>
              parse.mutateAsync({
                artifactId: row.artifactId,
                intent: actionIntent('POST', 'input.parse', row.artifactId),
              })
            }
          >
            {t('inputs.parse')}
          </AllowedActionButton>
          {row.parseRunId ? (
            <Link to={`/projects/${projectId}/inputs/${row.artifactId}/llm-log`}>
              <Button secondary small>
                {t('inputs.viewLlmLog')}
              </Button>
            </Link>
          ) : null}
          {status === 'PARSED' || status === 'NEEDS_REVIEW' ? (
            requirementsStageId ? (
              <Link
                to={`/projects/${projectId}/stages/${requirementsStageId}`}
                title={t('inputs.reviewParsedHint')}
              >
                <Button secondary small>
                  {t('inputs.reviewParsed')}
                </Button>
              </Link>
            ) : null
          ) : null}
          {status === 'NEEDS_REVIEW' && isTabular(row.parserType) ? (
            <Link to={`/projects/${projectId}/inputs/${row.artifactId}/column-mapping`}>
              <Button secondary small>
                {t('inputs.reviewMapping')}
              </Button>
            </Link>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

/** A scanned document is a supported upload and an unsupported parser case (AI-INP-003a). */
function explainError(t: TFunction, row: InputView): string {
  if (row.errorCode === 'SCANNED_DOCUMENT_UNSUPPORTED') return t('inputs.scannedUnsupported');
  return row.errorMessage ?? row.errorCode ?? '';
}

function UploadPanel({ projectId, workflowId }: { projectId: string; workflowId?: string }) {
  const { t } = useTranslation();
  const limits = useQuery(uploadLimitsOptions());
  const upload = useUploadInput(projectId);
  const [file, setFile] = useState<File | null>(null);
  const [classification, setClassification] = useState<string>('');
  const [phiDeclared, setPhiDeclared] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  /**
   * The upload result as the operator needs to see it after the bar is gone. `progress` reports
   * how much of the body left the browser — it reaches 1.0 on a 403 too — and the `finally` below
   * drops it, so without this line a finished upload and a refused one look identical. A refusal
   * still renders through `failure`, which reads the mutation's own error.
   */
  const [lastUpload, setLastUpload] = useState<{ name: string } | null>(null);

  const policy = limits.data;
  const tooBig = !!file && !!policy && file.size > policy.maxUploadBytes;
  const wrongType = !!file && !!policy?.allowedUploadTypes?.length && !policy.allowedUploadTypes.includes(file.type);
  const needsPhi = classification === 'PHI_PII' && !phiDeclared;
  const failure: ApiError | null = upload.error && isApiError(upload.error) ? upload.error : null;

  const submit = async () => {
    if (!file || !classification) return;
    const name = file.name;
    setProgress(0);
    setLastUpload(null);
    try {
      await upload.mutateAsync({
        projectId,
        file,
        dataClassification: classification as DataClassificationName,
        phiDeclared: classification === 'PHI_PII' && phiDeclared,
        onProgress: setProgress,
      });
      setLastUpload({ name });
      setFile(null);
      setClassification('');
      setPhiDeclared(false);
    } finally {
      setProgress(null);
    }
  };

  return (
    <SectionCard
      title={t('inputs.uploadTitle')}
      description={
        policy
          ? t('inputs.uploadLimits', {
              max: formatBytes(policy.maxUploadBytes),
              types: policy.allowedUploadTypes?.length ? policy.allowedUploadTypes.join(', ') : t('inputs.anyType'),
            })
          : t('inputs.limitsLoading')
      }
    >
      <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <label htmlFor="input-file">{t('inputs.chooseFile')}</label>
        <input
          id="input-file"
          type="file"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        {file ? (
          <span className="muted" style={{ fontSize: 12 }}>
            {file.name} · {formatBytes(file.size)}
          </span>
        ) : null}
      </div>
      {tooBig ? (
        <p role="alert" className="text-critical" style={{ margin: 0 }}>
          {t('inputs.tooBig', { max: formatBytes(policy?.maxUploadBytes ?? 0) })}
        </p>
      ) : null}
      {wrongType ? (
        <p role="alert" className="text-critical" style={{ margin: 0 }}>
          {t('inputs.wrongType', { types: (policy?.allowedUploadTypes ?? []).join(', ') })}
        </p>
      ) : null}
      {/* FR-REQ-001: there is no default choice. An undeclared upload is refused by S1, so
          preselecting a level here would record a declaration nobody made. */}
      <SelectInput
        label={t('inputs.classification')}
        required
        value={classification}
        onChange={setClassification}
        options={DATA_CLASSIFICATIONS.map((name) => ({
          value: name,
          label: t(`inputs.classes.${name}`, { defaultValue: name }),
        }))}
      />
      {classification === 'PHI_PII' ? (
        <>
          <CheckboxInput
            label={t('inputs.phiDeclaration')}
            checked={phiDeclared}
            onChange={setPhiDeclared}
          />
          {needsPhi ? (
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>{t('inputs.phiRequired')}</p>
          ) : null}
        </>
      ) : null}
      {progress !== null ? <LinearLoader amount={Math.round(progress * 100)} /> : null}
      {lastUpload ? (
        <p role="status" className="muted" style={{ margin: 0, fontSize: 12 }}>
          {t('inputs.uploadDone', { name: lastUpload.name })}
        </p>
      ) : null}
      {failure ? (
        <p role="alert" className="text-critical" style={{ margin: 0 }}>
          {failure.message ?? failure.errorCode}
        </p>
      ) : null}
      <div className="row" style={{ gap: 8 }}>
        <AllowedActionButton
          workflowId={workflowId}
          action={ACTION.inputUpload}
          loading={upload.isPending}
          disabled={!file || !classification || needsPhi || tooBig || wrongType}
          onExecute={submit}
        >
          {t('inputs.upload')}
        </AllowedActionButton>
      </div>
      </div>
    </SectionCard>
  );
}
