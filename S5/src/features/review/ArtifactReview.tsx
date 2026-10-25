/*
 * UI-REV-001: review one artifact — preview, raw content, diff against another version,
 * dependencies and sources, version, approval status, and a reproduce run with its hash answer.
 *
 * Every value here is S1's projection of the subsystem that owns it. The screen never recomputes a
 * hash, never infers an approval state from the status field, and never shows an empty box for a
 * purged artifact: FR-DATA-008 clears the bytes and keeps the record, so "the bytes are gone" is
 * the answer.
 *
 * The decision trail is read through the Registry view: an artifact carries no decision ids while
 * every registry entry does, so pointing at the object is the honest path instead of a second,
 * guessed copy of that link.
 */
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { capabilityLabel, classificationLabel } from '@/lib/labels';
import { useNavigate, useOutletContext, useParams, useSearchParams } from 'react-router-dom';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Button, NoticeBox, Tag } from '@dhis2/ui';
import { JsonEditor } from '@/components/JsonEditor';
import { NoticeBar } from '@/components/NoticeBar';
import { EmptyState } from '@/components/EmptyState';
import { Loading } from '@/lib/PageContainer';
import { isApiError } from '@/api/errors';
import {
  artifactContentOptions,
  artifactDiffOptions,
  artifactLineageOptions,
  artifactOptions,
  artifactFeedOptions,
} from '@/api/queries';
import { useReproduceArtifact } from '@/api/mutations';
import { ACTION, useAllowedActions } from '@/api/actions';
import { actionReasonText } from '@/components/AllowedActionButton';
import { formatBytes } from '@/lib/format';
import { looksTextual } from '@/lib/media';
import { formatDate } from '@/lib/time';
import { copyText, currentUrl } from '@/lib/clipboard';
import { pushToast } from '@/components/toast';
import type { ArtifactView } from '@/api/types';
import type { WorkspaceContext } from '@/features/workspace/WorkspaceContext';
import { artifactKindLabel, statusLabel } from '@/lib/labels';

const TABS = ['preview', 'raw', 'diff', 'lineage'] as const;
type Panel = (typeof TABS)[number];

/**
 * CP-UI-016: the bytes at their own address. S1 answers `?download=true` with an
 * `attachment` disposition and a filename (`routes/catalog.py`), so the browser streams the
 * artifact itself instead of this console holding 20 MB in React state to hand it back.
 */
export function artifactContentUrl(artifactId: string): string {
  return `/api/v1/artifacts/${encodeURIComponent(artifactId)}/content?download=true`;
}
const TAB_KEYS: Record<Panel, string> = {
  preview: 'review.tabPreview',
  raw: 'review.tabRaw',
  diff: 'review.tabDiff',
  lineage: 'review.tabLineage',
};

export function ArtifactReview() {
  const { t } = useTranslation();
  const { workflowId } = useOutletContext<WorkspaceContext>();
  // UI-REV-001: which artifact is open is part of the address, not component state. A review that
  // cannot be linked cannot be argued about — "the third one from the top" is not a citation, and a
  // reviewer sent to /artifacts lands on whichever row happens to sort first.
  const params = useParams();
  const navigate = useNavigate();
  const { projectId = '', artifactId: artifactIdFromUrl = '' } = params;
  const [typeFilter, setTypeFilter] = useState('');
  const list = useInfiniteQuery(artifactFeedOptions(workflowId, typeFilter || undefined));
  const artifacts = useMemo(() => list.data?.pages.flatMap((page) => page.items ?? []) ?? [], [list.data]);
  const current = artifactIdFromUrl || artifacts[0]?.id || null;
  const open = (id: string) => navigate(`/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(id)}`);
  const types = useMemo(
    () => [...new Set(artifacts.map((a) => a.type).filter((value): value is string => !!value))].sort(),
    [artifacts],
  );

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>{t('review.artifacts')}</h2>
          <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>{t('page.artifacts')}</p>
        </div>
        {types.length > 1 ? (
          <label className="filter-bar__check">
            {t('common.type')}{' '}
            <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
              <option value="">{t('common.all')}</option>
              {types.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      {list.isLoading ? <Loading /> : null}
      {!list.isLoading && !artifacts.length ? (
        <EmptyState icon="file" title={t('review.noArtifacts')}>
          {t('page.artifacts')}
        </EmptyState>
      ) : null}
      {artifacts.length ? (
        <div className="artifact-layout">
          <div className="artifact-layout__list">
            <p className="muted" style={{ fontSize: 12, margin: '0 0 6px' }}>
              {t('review.shownOf', { shown: artifacts.length, total: list.data?.pages[0]?.total ?? artifacts.length })}
            </p>
            <ul className="artifact-list" tabIndex={0} aria-label={t('review.artifacts')}>
              {artifacts.map((artifact) => (
                <li key={artifact.id}>
                  <button
                    type="button"
                    className="stage-item"
                    aria-current={artifact.id === current ? 'true' : undefined}
                    onClick={() => open(artifact.id)}
                  >
                    <span className="mono">{artifactKindLabel(artifact.type)}</span> · v{artifact.version ?? '—'} · {statusLabel(artifact.status) || '—'}
                  </button>
                </li>
              ))}
            </ul>
            {list.hasNextPage ? (
              <Button
                secondary
                small
                loading={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                {t('common.loadMore')}
              </Button>
            ) : null}
          </div>
          <div className="artifact-layout__detail">
            {current ? <ArtifactDetail artifactId={current} artifacts={artifacts} onSelect={open} /> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ArtifactDetail({
  artifactId,
  artifacts,
  onSelect,
}: {
  artifactId: string;
  artifacts: ArtifactView[];
  onSelect: (id: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [panel, setPanel] = useState<Panel>('preview');
  const [against, setAgainst] = useState('');
  const artifact = useQuery(artifactOptions(artifactId));
  const reproduce = useReproduceArtifact();
  // UI-BR-01: whether the bytes may be handed out is S1's answer. The workflow is the one this page
  // already stands in — the same answer every other control on the screen is gated against — rather
  // than a third request derived from the artifact row.
  const { workflowId: artifactWorkflowId } = useOutletContext<WorkspaceContext>();
  const downloadGate = useAllowedActions(artifactWorkflowId).check(ACTION.artifactDownload);
  // The list that linked here knows the uploaded filename; the artifact record does not. It is the
  // only evidence left when a browser uploaded a document with no media type of its own.
  const [search] = useSearchParams();
  const givenName = search.get('name') ?? '';
  const textual = !!artifact.data && !artifact.data.contentPurged && looksTextual(artifact.data.mediaType, givenName);
  // Bytes are fetched only for the tabs that show them: a 20 MB package should not be streamed to
  // render a header, and a purged artifact must not look like a download failure. A format this
  // device cannot decode is not fetched either — reading a .docx as text is mojibake, not a preview.
  const wantsContent = (panel === 'preview' || panel === 'raw') && textual;
  const content = useQuery(artifactContentOptions(wantsContent ? artifactId : undefined));
  const lineage = useQuery(artifactLineageOptions(panel === 'lineage' ? artifactId : undefined));
  const diff = useQuery(artifactDiffOptions(panel === 'diff' ? artifactId : undefined, against || undefined));

  if (artifact.isLoading) return <Loading />;
  const view = artifact.data;
  if (!view) return <NoticeBar tone="error">{t('errors.NOT_FOUND')}</NoticeBar>;
  const reproduced = reproduce.data;
  const siblings = artifacts.filter((other) => other.id !== artifactId);

  return (
    <div className="card">
      <div className="card__body stack" style={{ gap: 10 }}>
      <div className="artifact-toolbar">
        <Tag>{artifactKindLabel(view.type)}</Tag>
        <span className="artifact-meta">
          <span>{t('common.version')} {view.version ?? '—'} · {statusLabel(view.status) || '—'} · {formatBytes(view.size)}</span>
          <span>{formatDate(view.createdAt, i18n.language)}</span>
        </span>
      </div>
      <div className="artifact-meta mono">
        <span>{view.id}</span>
        <span>{view.contentHash ?? '—'}</span>
      </div>
      {/* The address of one artifact is the citation. It gets its own line so a long address wraps
          instead of pushing the controls out of the card. */}
      <div className="artifact-url">{currentUrl()}</div>
      <div className="artifact-toolbar">
        <Button
          secondary
          small
          onClick={() => {
            void copyText(currentUrl()).then((ok) =>
              pushToast({ tone: ok ? 'success' : 'error', title: ok ? t('review.copied') : t('review.copyFailed') }),
            );
          }}
        >
          {t('review.copyLink')}
        </Button>
        {view.contentPurged ? null : downloadGate.available ? (
          <a href={artifactContentUrl(String(view.id))} className="secondary-link">
            {t('common.download')}
          </a>
        ) : (
          <span className="muted" style={{ fontSize: 12 }}>{actionReasonText(t, downloadGate)}</span>
        )}
      </div>
      <div className="artifact-meta">
        <span>
          {t('review.creator')}: {view.creatorSubsystem ? capabilityLabel(view.creatorSubsystem) : '—'} / {view.creatorOperator ?? '—'}
        </span>
        <span>
          {t('inputs.classification')}: {classificationLabel(view.dataClassification) || '—'}
        </span>
        {view.prevVersionId ? (
          <span>
            {t('review.previousVersion')}: <span className="mono">{view.prevVersionId}</span>
          </span>
        ) : null}
      </div>

      {view.contentPurged ? (
        <NoticeBox warning title={t('review.purgedTitle')}>
          {t('review.purgedBody')}
        </NoticeBox>
      ) : null}

      <div className="route-tabs" role="tablist" aria-label={t('review.artifacts')}>
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={panel === name}
            className={panel === name ? 'route-tab route-tab--active' : 'route-tab'}
            onClick={() => setPanel(name)}
          >
            {t(TAB_KEYS[name])}
          </button>
        ))}
      </div>

      {panel === 'preview' ? (
        <Preview
          text={content.data}
          error={content.error}
          purged={view.contentPurged}
          notTextual={textual ? null : { type: String(view.mediaType ?? '—'), size: formatBytes(Number(view.size ?? 0)) }}
        />
      ) : null}

      {panel === 'raw' ?
        (!textual ? (
          <p className="muted">{t('review.notTextual', { type: String(view.mediaType ?? '—'), size: formatBytes(Number(view.size ?? 0)) })}</p>
        ) : content.data ? (
          <JsonEditor value={content.data} readOnly height="360px" />
        ) : (
          <p className="muted">{t('review.noContent')}</p>
        ))
      : null}

      {panel === 'diff' ? (
        <div className="stack" style={{ gap: 8 }}>
          <label className="muted" style={{ fontSize: 12 }}>
            {t('review.diffAgainst')}{' '}
            <select value={against} onChange={(event) => setAgainst(event.target.value)}>
              <option value="">—</option>
              {siblings.map((sibling) => (
                <option key={sibling.id} value={sibling.id}>
                  {`${sibling.type ?? ''} v${sibling.version ?? ''} ${sibling.id}`}
                </option>
              ))}
            </select>
          </label>
          {!against ? <p className="muted">{t('review.diffPick')}</p> : null}
          {against && diff.isLoading ? <Loading /> : null}
          {against && diff.data ? (
            diff.data.changed ? (
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {(diff.data.lines ?? []).map((line, index) => (
                  <li key={index} className="mono">
                    {JSON.stringify(line)}
                  </li>
                ))}
              </ul>
            ) : (
              <NoticeBox valid title={t('review.diffNone')}>
                {diff.data.kind ?? ''}
              </NoticeBox>
            )
          ) : null}
        </div>
      ) : null}

      {panel === 'lineage' ? (
        <div className="stack" style={{ gap: 6 }}>
          <p className="muted" style={{ fontSize: 12 }}>{t('review.lineageHint')}</p>
          <IdList title={t('review.sources')} ids={lineage.data?.sources ?? []} onSelect={onSelect} />
          <IdList title={t('review.derived')} ids={lineage.data?.derived ?? []} onSelect={onSelect} />
        </div>
      ) : null}

      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <Button secondary loading={reproduce.isPending} onClick={() => reproduce.mutate({ artifactId })}>
          {t('review.reproduce')}
        </Button>
        {reproduced ?
          (reproduced.matches ? (
            <NoticeBox valid title={t('review.reproduceMatch')}>
              <span className="mono">{reproduced.hash}</span>
            </NoticeBox>
          ) : (
            <NoticeBox error title={t('review.reproduceMismatch')}>
              <span className="mono">{reproduced.hash}</span>
            </NoticeBox>
          ))
        : null}
        {reproduce.error && isApiError(reproduce.error) ? (
          <NoticeBox error title={t('error.title')}>{reproduce.error.message ?? reproduce.error.errorCode}</NoticeBox>
        ) : null}
      </div>
      <p className="muted" style={{ fontSize: 12 }}>{t('review.decisionHint')}</p>
      </div>
    </div>
  );
}

function IdList({ title, ids, onSelect }: { title: string; ids: string[]; onSelect: (id: string) => void }) {
  return (
    <div>
      <strong>{title}</strong>
      {ids.length === 0 ? <p className="muted">—</p> : null}
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {ids.map((id) => (
          <li key={id}>
            <button type="button" className="stage-item" onClick={() => onSelect(id)}>
              <span className="mono">{id}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Preview({
  text,
  error,
  purged,
  notTextual,
}: {
  text?: string;
  error?: unknown;
  purged?: boolean;
  /** Set when this device cannot render the format: what to say instead of a box of mojibake. */
  notTextual?: { type: string; size: string } | null;
}): ReactNode {
  const { t } = useTranslation();
  // The banner above already states the purge; repeating it puts one fact on screen twice.
  if (purged) return null;
  if (isApiError(error) && error.errorCode === 'ARTIFACT_PURGED') return <p className="muted">{t('review.purgedBody')}</p>;
  if (notTextual) return <p className="muted">{t('review.notTextual', notTextual)}</p>;
  if (!text) return <p className="muted">{t('review.noContent')}</p>;
  let pretty = text;
  try {
    pretty = JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    // Not JSON — an uploaded document or a binary package is shown as-is, never reformatted.
  }
  return (
    // the preview scrolls, so it has to be reachable by keyboard as well (WCAG 2.1.1)
    <pre
      className="mono"
      tabIndex={0}
      style={{ maxHeight: 320, overflow: 'auto', background: '#f7f7f7', padding: 8, margin: 0 }}
    >
      {pretty}
    </pre>
  );
}
