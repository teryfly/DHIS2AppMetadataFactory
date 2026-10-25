/*
 * UI-REV-004: the API review surface, read-only.
 *
 * Two screens, one document. `ApiReviewView` is the per-page card list that fits inside the project
 * workspace; `ApiDocView` is the full browser at its own URL, so a reviewer can quote "operation X
 * of the version we approved" into a review record instead of describing a screen.
 *
 * Neither may gain a control that sends a request to a target DHIS2 instance, and neither may proxy
 * one through S1 (总体 SRS C-08; S4 DX-BR-01 reserves DHIS2 access for S4, behind approval and
 * commit). The property is enforced by `isolation.test.ts`, which scans this file's directory for
 * interactive-request vocabulary and for outbound primitives.
 *
 * S2 writes the artifact as YAML (`api/openapi.yaml`), so the reader is the shared OpenAPI model
 * rather than `JSON.parse` — an earlier desktop attempt parsed JSON only and rendered no operations.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { NoticeBar } from '@/components/NoticeBar';
import { EmptyState } from '@/components/EmptyState';
import { Heading } from '@/components/typography';
import { TextInput } from '@/components/form';
import { projectOptions, workflowArtifactsOptions, artifactContentOptions } from '@/api/queries';
import { parseApiDocument } from '@/lib/openapi/parseDoc';
import { buildDocModel } from '@/lib/openapi/docModel';
import type { ApiDocModel, ApiOperation } from '@/lib/openapi/docModel';

/** The compiled OpenAPI artifact of the project's workflow, parsed. */
function useProjectApiDoc() {
  const { projectId = '' } = useParams();
  const project = useQuery(projectOptions(projectId));
  const workflowId = project.data?.workflowId ?? undefined;
  const artifacts = useQuery(workflowArtifactsOptions(workflowId ?? ''));
  const artifactId = useMemo(
    () => String((artifacts.data?.items ?? []).find((a) => String(a.type ?? '') === 'OPENAPI')?.id ?? ''),
    [artifacts.data],
  );
  const content = useQuery(artifactContentOptions(artifactId || undefined));

  const rawContent: unknown = content.data;
  const text = rawContent == null ? null : typeof rawContent === 'string' ? rawContent : JSON.stringify(rawContent);
  const parsed = parseApiDocument(text);
  const model = parsed.ok ? buildDocModel(parsed) : null;

  return {
    projectId,
    workflowId,
    artifactId,
    loading: Boolean(project.isLoading || artifacts.isLoading || content.isLoading),
    failed: Boolean(artifacts.isError || content.isError),
    noArtifact: !artifactId,
    parsed,
    model: model as ApiDocModel | null,
  };
}

function SchemaTypes({ value }: { value: unknown }) {
  if (value == null) return <span className="muted">—</span>;
  const record = value as Record<string, unknown>;
  const type = Array.isArray(record.type) ? record.type.join(' | ') : String(record.type ?? 'object');
  const extras = [
    record.format ? String(record.format) : '',
    record.enum ? `enum: ${(record.enum as unknown[]).slice(0, 6).join('|')}` : '',
    record.$ref ? String(record.$ref) : '',
    record.ref ? String(record.ref) : '',
  ].filter(Boolean);
  return (
    <span className="mono" style={{ fontSize: 12 }}>
      {type}
      {extras.length ? <span className="muted"> {'· '} {extras.join(' · ')}</span> : null}
    </span>
  );
}

function OperationTable({ op }: { op: ApiOperation }) {
  const { t } = useTranslation();
  return (
    <div className="stack" style={{ gap: 8 }}>
      {op.parameters.length ? (
        <DataTable>
          <DataTableHead>
            <DataTableRow>
              <DataTableCell tag="th">{t('api.paramName')}</DataTableCell>
              <DataTableCell tag="th">{t('api.paramIn')}</DataTableCell>
              <DataTableCell tag="th">{t('api.paramSchema')}</DataTableCell>
              <DataTableCell tag="th">{t('common.status')}</DataTableCell>
            </DataTableRow>
          </DataTableHead>
          <DataTableBody>
            {op.parameters.map((p, i) => (
              <DataTableRow key={`${p.name}-${i}`}>
                <DataTableCell><span className="mono">{p.name}</span></DataTableCell>
                <DataTableCell>{p.location}</DataTableCell>
                <DataTableCell><SchemaTypes value={p.schema} /></DataTableCell>
                <DataTableCell>
                  {p.required ? <Tag positive>{t('api.required')}</Tag> : <Tag neutral>{t('api.optional')}</Tag>}
                  {p.deprecated ? <Tag negative>{t('api.deprecated')}</Tag> : null}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      ) : (
        <p className="muted">{t('api.noParameters')}</p>
      )}

      {op.body ? (
        <div>
          <span className="muted">{t('api.requestBody')} · {op.body.contentType}</span>
          <SchemaTypes value={op.body.schema} />
        </div>
      ) : null}

      {op.responses.map((r) => (
        <div key={r.status} className="row" style={{ gap: 8, alignItems: 'baseline' }}>
          <Tag neutral>{r.status}</Tag>
          <span className="muted">{r.description || '—'}</span>
          {r.hasSchema ? <SchemaTypes value={r.schema} /> : <span className="muted">{t('api.noBody')}</span>}
        </div>
      ))}
      {op.errorResponses.length === 0 ? <p className="muted">{t('api.noErrors')}</p> : null}
      {op.registryRefs.length ? (
        <p className="muted mono" style={{ fontSize: 12 }}>{t('api.registryRefs')}: {op.registryRefs.join(', ')}</p>
      ) : null}
      {op.security.length ? <p className="muted" style={{ fontSize: 12 }}>{t('api.security')}: {op.security.join(', ')}</p> : null}
    </div>
  );
}

function DocNotice({ doc }: { doc: ReturnType<typeof useProjectApiDoc> }) {
  const { t } = useTranslation();
  if (doc.parsed.ok) return null;
  return (
    <NoticeBar tone="error" title={t('api.unreadable')}>
      {t(`api.parse.${doc.parsed.reason}`)} · {doc.parsed.message || t('api.parseShapeNote')}
    </NoticeBar>
  );
}

export function ApiReviewView() {
  const { t } = useTranslation();
  const { projectId } = useParams();
  const doc = useProjectApiDoc();
  if (doc.loading) return <Loading />;

  return (
    <PageContainer
      title={t('api.title')}
      description={t('page.api')}
      helpKey="artifacts"
      actions={doc.model ? <Link to={`/projects/${projectId}/api/doc`} className="secondary-link">{t('api.openDoc')}</Link> : undefined}
    >
      <div className="stack">
        {doc.noArtifact ? (
          <EmptyState>
            <p>{t('api.noneTitle')}</p>
            <p className="muted">{t('api.noneBody')}</p>
          </EmptyState>
        ) : null}
        {doc.failed ? <NoticeBar tone="error" title={t('error.title')}>{t('api.readFailed')}</NoticeBar> : null}
        <DocNotice doc={doc} />
        {(doc.model?.operations ?? []).map((op) => (
          <Card key={op.id}>
            <div className="stack" style={{ gap: 4 }}>
              <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
                <Tag neutral>{op.method}</Tag>
                <span className="mono">{op.path}</span>
                {op.deprecated ? <Tag negative>{t('api.deprecated')}</Tag> : null}
              </div>
              <strong>{op.summary || op.id}</strong>
              {op.tags.length ? <span className="muted">{op.tags.join(', ')}</span> : null}
            </div>
          </Card>
        ))}
      </div>
    </PageContainer>
  );
}

export function ApiDocView() {
  const { t } = useTranslation();
  const doc = useProjectApiDoc();
  const [needle, setNeedle] = useState('');
  const [open, setOpen] = useState<string | null>(null);

  if (doc.loading) return <Loading />;
  const model = doc.model;
  const operations = model?.operations ?? [];
  const matched = needle.trim()
    ? operations.filter((op) =>
        `${op.id} ${op.method} ${op.path} ${op.summary} ${op.tags.join(' ')}`.toLowerCase().includes(needle.trim().toLowerCase()),
      )
    : operations;

  return (
    <PageContainer title={t('api.doc.title')} description={t('page.api')} helpKey="artifacts">
      <div className="stack">
        {doc.noArtifact || !model ? (
          <EmptyState>
            <p>{t('api.noneTitle')}</p>
            <p className="muted">{t('api.noneBody')}</p>
          </EmptyState>
        ) : null}
        <DocNotice doc={doc} />
        {model ? (
          <>
            <Card>
              <div className="stack" style={{ gap: 4 }}>
                <Heading level={5} size="small">{model.info.title || t('api.title')}</Heading>
                <p className="muted">
                  {t('api.specVersion', { version: model.specVersion })} · {model.format.toUpperCase()} ·{' '}
                  {t('api.counts', { ops: model.counts.operations, paths: model.counts.paths, schemas: model.counts.schemas })}
                </p>
                {model.info.description ? <p>{model.info.description}</p> : null}
                {model.servers.length ? (
                  <p className="muted mono" style={{ fontSize: 12 }}>
                    {model.servers.map((s) => s.url).join(' · ')}
                  </p>
                ) : null}
                {model.warnings.length ? (
                  <NoticeBar tone="warning" title={t('api.warnings')}>
                    {model.warnings.map((w) => t(`api.warning.${w}`, { defaultValue: w })).join(' · ')}
                  </NoticeBar>
                ) : null}
                <TextInput label={t('api.search')} value={needle} onChange={setNeedle} />
              </div>
            </Card>

            {model.tags.map((tag) => {
              const inTag = matched.filter((op) => op.tags.includes(tag.name));
              if (inTag.length === 0) return null;
              return (
                <Card key={tag.name}>
                  <div className="stack" style={{ gap: 8 }}>
                    <Heading level={5} size="small">{tag.name} ({inTag.length})</Heading>
                    {inTag.map((op) => (
                      <div key={op.id} className="stack" style={{ gap: 2 }}>
                        <button
                          type="button"
                          className="row"
                          style={{ gap: 8, alignItems: 'baseline', background: 'none', border: 0, textAlign: 'left', padding: 0 }}
                          onClick={() => setOpen(open === op.id ? null : op.id)}
                          aria-expanded={open === op.id}
                        >
                          <Tag neutral>{op.method}</Tag>
                          <span className="mono">{op.path}</span>
                          <span>{op.summary || op.id}</span>
                        </button>
                        {open === op.id ? <OperationTable op={op} /> : null}
                      </div>
                    ))}
                  </div>
                </Card>
              );
            })}

            {matched.length === 0 && needle ? <p className="muted">{t('api.noMatches')}</p> : null}

            <Card>
              <div className="stack" style={{ gap: 4 }}>
                <Heading level={5} size="small">{t('api.schemas')} ({model.schemas.length})</Heading>
                {model.schemas.map((s) => (
                  <div key={s.name} className="row" style={{ gap: 8 }}>
                    <span className="mono">{s.name}</span>
                    <span className="muted">{String((s.schema as Record<string, unknown> | undefined)?.type ?? 'object')}</span>
                  </div>
                ))}
              </div>
            </Card>
          </>
        ) : null}
      </div>
    </PageContainer>
  );
}
