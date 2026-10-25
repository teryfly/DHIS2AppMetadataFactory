/*
 * UI-IA-001 "Handoff": the mode-B development package, file by file (CP-UI-021).
 *
 * This is what a frontend team is actually given. S1 forwards S2's own listing and never unpacks
 * it, so the layout on this screen is S2's (BP-HO-004) and `kind` is deliberately S2's word, not
 * the build package's `role` — the two packages answer different questions and merging their
 * vocabulary here would hide that.
 *
 * The integrity check is not computed here either. It is part of the HANDOFF stage's result, so
 * the screen says where it lives and links there, rather than restating a verdict it cannot see.
 *
 * "No package yet" is a state S1 answers twice: `allowed-actions` withholds
 * `frontend_package.download` with `PACKAGE_NOT_GENERATED` (domain/actions.py), and the read itself
 * answers 404 with `reason=NOT_GENERATED` and `requiredStage=HANDOFF` (CP-UI-022). The gate is the
 * one UI-BR-01 allows as the reason to not ask at all, so this page asks it first and only fires the
 * read when S1 says there is something to read. Either answer is guidance, not a failure: naming the
 * stage that produces the package is the whole point.
 */
import { useState } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, DataTable, DataTableBody, DataTableCell, DataTableHead, DataTableRow, Tag } from '@dhis2/ui';
import { JsonEditor } from '@/components/JsonEditor';
import { EmptyState } from '@/components/EmptyState';
import { Heading } from '@/components/typography';
import { NoticeBar } from '@/components/NoticeBar';
import { actionReasonText } from '@/components/AllowedActionButton';
import { Loading, PageContainer } from '@/lib/PageContainer';
import { isApiError } from '@/api/errors';
import { ACTION, useAllowedActions } from '@/api/actions';
import { frontendPackageFileOptions, frontendPackageOptions } from '@/api/queries';
import { formatBytes } from '@/lib/format';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';
import { handoffKindLabel } from '@/lib/labels';

const P = '/api/v1';

/** A file's own address, for the browser to stream: `download=true` names it per CP-UI-016. */
export function frontendPackageFileUrl(projectId: string, path: string): string {
  return `${P}/projects/${encodeURIComponent(projectId)}/frontend-package/files/${path.split('/').map(encodeURIComponent).join('/')}?download=true`;
}

export function HandoffView() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { projectId, workflowId, project } = useOutletContext<WorkspaceContext>();
  const { check, isLoading: gateLoading } = useAllowedActions(workflowId);
  const gate = check(ACTION.frontendPackageDownload);
  const pkg = useQuery(frontendPackageOptions(projectId, gate.available));
  const [openPath, setOpenPath] = useState('');
  const file = useQuery(frontendPackageFileOptions(projectId, openPath || undefined));

  if (gateLoading && !gate.available) return <Loading />;
  if (pkg.isPending && gate.available && !pkg.data) return <Loading />;

  if (!gate.available || (isApiError(pkg.error) && pkg.error.errorCode === 'NOT_FOUND')) {
    const missing = isApiError(pkg.error) ? pkg.error : null;
    const notExist = missing?.details.reason === 'NOT_EXIST';
    const requiredStage = String(missing?.details.requiredStage ?? 'HANDOFF');
    const stage = project.stages?.find((s) => s.stageType === requiredStage);
    const label = t(`stageTypes.${requiredStage}`, { defaultValue: requiredStage });
    // A withheld action is not always "not generated yet": S1 blocks the whole forwarding list with
    // PROJECT_ARCHIVED, and claiming the stage still has to run would be the UI telling a story the
    // server did not tell. S1's own reason wins whenever it differs.
    const withheld = !gate.available && gate.reasonCode !== 'PACKAGE_NOT_GENERATED';
    return (
      <PageContainer title={t('handoff.title')} description={t('page.handoff')} helpKey="delivery">
        <Card>
          <EmptyState>
            <div className="stack">
              <Heading level={3} size="medium">{t('handoff.notGeneratedTitle')}</Heading>
              <p>{withheld ? actionReasonText(t, gate) : notExist ? t('handoff.notExistBody') : t('handoff.notGeneratedBody', { stage: label })}</p>
              {!withheld && stage?.id ? (
                <Button primary onClick={() => navigate(`/projects/${projectId}/stages/${String(stage.id)}`)}>
                  {t('handoff.notGeneratedAction', { stage: label })}
                </Button>
              ) : null}
            </div>
          </EmptyState>
        </Card>
      </PageContainer>
    );
  }
  if (pkg.isError) {
    return (
      <PageContainer title={t('handoff.title')} description={t('page.handoff')} helpKey="delivery">
        <NoticeBar tone="error" title={t('error.title')}>
          {isApiError(pkg.error) ? pkg.error.message : String(pkg.error)}
        </NoticeBar>
      </PageContainer>
    );
  }

  const view = pkg.data;
  const files = view?.files ?? [];
  const handoff = project.stages?.find((s) => s.stageType === 'HANDOFF');

  return (
    <PageContainer title={t('handoff.title')} description={t('page.handoff')} helpKey="delivery">
      <div className="stack">
        <p className="muted">{t('handoff.body')}</p>
        <div className="row" style={{ gap: 16, flexWrap: 'wrap', fontSize: 12 }}>
          <span>
            {t('handoff.blueprintVersion')}: <span className="mono">{view?.blueprintVersion || '—'}</span>
          </span>
          {view?.artifactId ? (
            <span>
              {t('handoff.packageArtifact')}:{' '}
              {/* UI-REV-001: the package is an artifact like any other, and its review page —
                  content, lineage, reproduce — is one link away rather than a second copy here. */}
              <Link to={`/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(view.artifactId)}`}>
                <span className="mono">{view.artifactId}</span>
              </Link>
            </span>
          ) : null}
          {handoff?.id ? (
            <span>
              {t('handoff.stage')}:{' '}
              <Link to={`/projects/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(String(handoff.id))}`}>
                {t(`stageStatus.${handoff.status ?? ''}`, { defaultValue: String(handoff.status ?? '—') })}
              </Link>
            </span>
          ) : null}
        </div>
        <p className="muted" style={{ fontSize: 12 }}>{t('handoff.integrityHint')}</p>

        {!files.length ? <p className="muted">{t('handoff.noFiles')}</p> : null}
        {files.length ? (
          <DataTable>
            <DataTableHead>
              <DataTableRow>
                <DataTableCell tag="th">{t('handoff.file')}</DataTableCell>
                <DataTableCell tag="th">{t('handoff.kind')}</DataTableCell>
                <DataTableCell tag="th">{t('handoff.size')}</DataTableCell>
                <DataTableCell tag="th">{t('handoff.hash')}</DataTableCell>
                <DataTableCell tag="th">{t('common.actions')}</DataTableCell>
              </DataTableRow>
            </DataTableHead>
            <DataTableBody>
              {files.map((f) => (
                <DataTableRow key={f.path}>
                  <DataTableCell>
                    <span className="mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>{f.path}</span>
                  </DataTableCell>
                  <DataTableCell>
                    <Tag>{handoffKindLabel(f.kind ?? 'DOCUMENT')}</Tag>
                  </DataTableCell>
                  <DataTableCell>{formatBytes(f.sizeBytes ?? 0)}</DataTableCell>
                  <DataTableCell>
                    <span className="muted mono" style={{ fontSize: 12 }}>{String(f.contentHash ?? '—').slice(0, 12)}</span>
                  </DataTableCell>
                  <DataTableCell>
                    <div className="row" style={{ gap: 8 }}>
                      <Button secondary small onClick={() => setOpenPath(f.path)}>
                        {t('handoff.view')}
                      </Button>
                      {/* A link, not a button around a link: the browser streams the file, and a
                          nested interactive control is both an a11y violation and a worse download
                          than the one the operating system already does well. */}
                      <a href={frontendPackageFileUrl(projectId, f.path)}>{t('handoff.download')}</a>
                    </div>
                  </DataTableCell>
                </DataTableRow>
              ))}
            </DataTableBody>
          </DataTable>
        ) : null}

        {openPath ? (
          <Card>
            <div className="stack" style={{ gap: 8 }}>
              <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                <Heading level={5} size="small">
                  <span className="mono">{openPath}</span>
                </Heading>
                <Button secondary small onClick={() => setOpenPath('')}>{t('handoff.close')}</Button>
              </div>
              {file.isLoading ? <Loading /> : null}
              {file.isError ? (
                <NoticeBar tone="error" title={t('error.title')}>
                  {isApiError(file.error) ? file.error.message : String(file.error)}
                </NoticeBar>
              ) : null}
              {file.data != null ? <JsonEditor value={file.data} readOnly height="360px" /> : null}
            </div>
          </Card>
        ) : null}
      </div>
    </PageContainer>
  );
}
