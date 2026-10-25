/*
 * UI-REQS-004 block ①: what S3 understood the inputs to say, before it becomes a draft entry.
 *
 * The list is S1's projection of the NRR artifact (S5IF-BP15) grouped by category, each item
 * carrying the source input and the confidence S3 assigned. The grouping is asked of S1 rather
 * than done here because the filter set — category, source input, confidence — is server-side
 * (CP-UI-003), and a low-confidence item is a reason to look at the input again, not a reason to
 * hide it: nothing in this view edits anything.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, SegmentedControl, Tag } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { Loading } from '@/lib/PageContainer';
import { nrrOptions } from '@/api/queries';
import { nrrCategoryLabel, nrrPriorityLabel, nrrStatusLabel } from '@/lib/labels';
import type { NrrItemView } from '@/api/types';

/** Below this, S3 is guessing; the number is S1's default threshold, not a UI opinion. */
const LOW_CONFIDENCE = 0.6;

export function NrrCard({
  workflowId,
  projectId,
  stageId,
  inputArtifactId,
}: {
  workflowId: string;
  projectId: string;
  /** Set by the inputs page, where the next step is answering questions on this stage. */
  stageId?: string;
  inputArtifactId?: string;
}) {
  const { t } = useTranslation();
  const [category, setCategory] = useState<string>('');
  const all = useQuery(nrrOptions(workflowId, { inputArtifactId }));
  const grouped = useQuery(
    nrrOptions(workflowId, { inputArtifactId, ...(category ? { category } : {}) }),
  );

  if (all.isLoading) return <Loading />;
  const items = grouped.data?.items ?? [];
  const total = grouped.data?.total ?? items.length;
  const categories = [...new Set((all.data?.items ?? []).map((item) => item.category ?? '—'))].sort();
  const hasCandidates = items.some((item) => item.status === 'CANDIDATE');

  return (
    <Card>
      <div className="stack" style={{ gap: 8 }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <Heading level={4} size="small">{t('nrr.title')}</Heading>
          {stageId ? (
            <Link to={`/projects/${projectId}/stages/${stageId}`}>
              <Button secondary small>{t('nrr.reviewQuestions')}</Button>
            </Link>
          ) : (
            <Link to={`/projects/${projectId}/inputs`}>
              <Button secondary small>{t('nrr.goToInputs')}</Button>
            </Link>
          )}
        </div>
        <p className="muted" style={{ fontSize: 12 }}>{t('nrr.hint')}</p>
        {hasCandidates ? <p className="muted" style={{ fontSize: 12 }}>{t('nrr.candidateNote')}</p> : null}
        {items.length ? (
          <p className="muted" style={{ fontSize: 12 }}>{t('nrr.counts', { shown: items.length, total })}</p>
        ) : null}
        {categories.length > 1 ? (
          <SegmentedControl
            selected={category}
            options={[
              { label: t('common.all'), value: '' },
              ...categories.map((name) => ({ label: nrrCategoryLabel(name), value: name })),
            ]}
            onChange={({ value }: { value: string }) => setCategory(value)}
          />
        ) : null}
        {!items.length ? (
          <div className="stack" style={{ gap: 4 }}>
            <p className="muted">{t('nrr.empty')}</p>
            <Link to="/help/tutorial">{t('nrr.emptyHelp')}</Link>
          </div>
        ) : null}
        {items.map((item, index) => (
          <NrrItem key={`${item.businessKey ?? 'row'}-${index}`} item={item} projectId={projectId} />
        ))}
      </div>
    </Card>
  );
}

function NrrItem({ item, projectId }: { item: NrrItemView; projectId: string }) {
  const { t } = useTranslation();
  const confidence = item.confidence ?? null;
  const low = confidence !== null && confidence < LOW_CONFIDENCE;

  return (
    <div className="stack" style={{ gap: 4, borderTop: '1px solid #e8e8e8', paddingTop: 8 }}>
      <div className="row" style={{ gap: 8, alignItems: 'baseline' }}>
        <Tag>{nrrCategoryLabel(item.category) || '—'}</Tag>
        {item.priority ? <Tag>{nrrPriorityLabel(item.priority)}</Tag> : null}
        {confidence !== null ? (
          <span className="muted" style={{ fontSize: 12 }}>
            {t('nrr.confidence', { value: Math.round(confidence * 100) })}
          </span>
        ) : null}
      </div>
      <div>{item.statement ?? item.businessKey ?? '—'}</div>
      <div className="muted" style={{ fontSize: 12 }}>
        {/* The source is the uploaded material itself. A column-mapping page was the wrong
            destination for every text input: it reviewed a table that does not exist. */}
        {t('nrr.source')}:{' '}
        {item.source?.artifactId ? (
          <Link
            to={`/projects/${projectId}/artifacts/${String(item.source.artifactId)}`}
            className="mono"
          >
            {String(item.source.artifactId)}
          </Link>
        ) : (
          <span className="mono">{item.artifactId ?? '—'}</span>
        )}
        {item.status ? ` · ${nrrStatusLabel(item.status)}` : ''}
      </div>
      {low && item.openQuestions?.length ? (
        <p role="note" className="text-warning" style={{ fontSize: 12, margin: 0 }}>
          {t('nrr.lowConfidence', { question: item.openQuestions[0] })}
        </p>
      ) : null}
    </div>
  );
}
