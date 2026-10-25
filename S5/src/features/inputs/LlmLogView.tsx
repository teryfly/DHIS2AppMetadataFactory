/*
 * S5IF-INP08/INP09: the LLM conversation behind one business input.
 *
 * A parse is a chain of model calls, and when one fails the useful question is what the model
 * was asked. This page shows each attempt (including transport retries), the fenced input chunk
 * that went out, and the raw answer that came back. The provider is non-streaming, so while an
 * attempt is in flight the page shows the request and waits — there are no tokens to stream.
 *
 * Reading is deliberately lazy: a 40-chunk parse would otherwise ship every answer at once, so
 * the list is summaries and a card fetches its own row when expanded.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { Button, Tag } from '@dhis2/ui';
import { NoticeBar } from '@/components/NoticeBar';
import { Loading } from '@/lib/PageContainer';
import { formatBytes } from '@/lib/format';
import { formatRelativeTime } from '@/lib/time';
import { inputsOptions, llmExchangeOptions, llmLogOptions } from '@/api/queries';
import type { LlmExchangeSummaryView, LlmExchangeDetailView } from '@/api/types';

export function LlmLogView() {
  const { t } = useTranslation();
  const { projectId = '', artifactId = '' } = useParams();
  const log = useQuery(llmLogOptions(projectId, artifactId));
  const inputs = useQuery(inputsOptions(projectId));
  const [openId, setOpenId] = useState<string | null>(null);

  if (log.isLoading) return <Loading />;

  const input = (inputs.data?.items ?? []).find((item) => item.artifactId === artifactId);
  const status = log.data?.parseStatus ?? input?.parseStatus ?? null;
  const items = log.data?.items ?? [];

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h3 style={{ margin: 0 }}>{t('llmLog.title')}</h3>
          <div className="muted" style={{ fontSize: 12 }}>
            {input?.filename ?? artifactId}
          </div>
        </div>
        <Link to={`/projects/${encodeURIComponent(projectId)}/inputs`}>
          <Button secondary small>
            {t('llmLog.back')}
          </Button>
        </Link>
      </div>

      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <Tag>{status ? t(`inputs.status.${status}`, { defaultValue: status }) : t('inputs.status.NONE')}</Tag>
        {status === 'PARSING' ? <span className="muted">{t('llmLog.polling')}</span> : null}
      </div>

      {log.isError ? <NoticeBar tone="error">{t('llmLog.loadFailed')}</NoticeBar> : null}
      {!items.length ? <p className="muted">{t('llmLog.empty')}</p> : null}

      {items.map((item) => (
        <ExchangeCard
          key={item.exchangeId}
          projectId={projectId}
          artifactId={artifactId}
          item={item}
          open={openId === item.exchangeId}
          onToggle={() => setOpenId((current) => (current === item.exchangeId ? null : item.exchangeId))}
        />
      ))}
    </div>
  );
}

function statusLabel(t: (key: string, options?: Record<string, unknown>) => string, status: string): string {
  return t(`llmLog.exchangeStatus.${status}`, { defaultValue: status });
}function ExchangeCard({
  projectId,
  artifactId,
  item,
  open,
  onToggle,
}: {
  projectId: string;
  artifactId: string;
  item: LlmExchangeSummaryView;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const detail = useQuery(llmExchangeOptions(projectId, artifactId, open ? item.exchangeId : undefined));

  return (
    <div className="stack" style={{ gap: 8, border: '1px solid var(--colors-grey300)', borderRadius: 4, padding: 12 }}>
      <div className="row" style={{ gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>#{item.sequence}</strong>
        <Tag>{statusLabel(t, item.status ?? 'IN_FLIGHT')}</Tag>
        <span className="muted">{item.model}</span>
        {item.latencyMs !== null && item.latencyMs !== undefined ? (
          <span className="muted">{formatDuration(item.latencyMs)}</span>
        ) : null}
        <span className="muted">
          {t('llmLog.tokens', { input: item.inputTokens ?? 0, output: item.outputTokens ?? 0 })}
        </span>
        <span className="muted">{item.createdAt ? formatRelativeTime(item.createdAt, t) : '—'}</span>
        <span style={{ marginLeft: 'auto' }}>
          <Button small secondary onClick={onToggle}>
            {open ? t('llmLog.hide') : t('llmLog.show')}
          </Button>
        </span>
      </div>

      {item.errorCode ? (
        <NoticeBar tone="error">
          {item.errorMessage ? `${item.errorCode}: ${item.errorMessage}` : item.errorCode}
        </NoticeBar>
      ) : null}

      {open ? <ExchangeDetail query={detail} /> : null}
    </div>
  );
}

function ExchangeDetail({ query }: { query: UseQueryResult<LlmExchangeDetailView> }) {
  const { t } = useTranslation();
  if (query.isLoading) return <Loading />;
  if (query.isError || !query.data) return <NoticeBar tone="error">{t('llmLog.detailFailed')}</NoticeBar>;
  const detail = query.data;
  const response = detail.response;

  return (
    <div className="stack" style={{ gap: 12 }}>
      {detail.status === 'IN_FLIGHT' ? (
        <NoticeBar tone="info">{t('llmLog.inFlight')}</NoticeBar>
      ) : null}

      <section>
        <div className="muted" style={{ fontSize: 12, textTransform: 'uppercase' }}>
          {t('llmLog.request')}
        </div>
        <div className="stack" style={{ gap: 8 }}>
          {detail.request.messages?.map((message, index) => (
            <div key={`${message.role}-${index}`} style={{ background: 'var(--colors-grey100)', padding: 8, borderRadius: 4 }}>
              <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                <Tag>{message.role}</Tag>
                {message.images?.map((image) => (
                  <span key={image.label} className="muted" style={{ fontSize: 12 }}>
                    {t('llmLog.image', {
                      label: image.label,
                      mediaType: image.mediaType,
                      size: formatBytes(image.sizeBytes),
                    })}
                  </span>
                ))}
              </div>
              <pre className="mono" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: '8px 0 0' }}>
                {message.content}
              </pre>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="muted" style={{ fontSize: 12, textTransform: 'uppercase' }}>
          {t('llmLog.response')}
        </div>
        {response?.storageTruncated ? <NoticeBar tone="warning">{t('llmLog.storageTruncated')}</NoticeBar> : null}
        {response && response.text ? (
          <pre className="mono" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
            {response.text}
          </pre>
        ) : (
          <p className="muted">{t('llmLog.noResponse')}</p>
        )}
      </section>

      <section>
        <div className="muted" style={{ fontSize: 12, textTransform: 'uppercase' }}>
          {t('llmLog.metadata')}
        </div>
        <ul style={{ margin: 0, paddingLeft: 18 }} className="muted">
          <li>{t('llmLog.schema', { name: detail.request.schemaName ?? '—' })}</li>
          <li>{t('llmLog.temperature', { value: detail.request.temperature ?? '—' })}</li>
          <li>{t('llmLog.maxTokens', { value: detail.request.maxOutputTokens ?? '—' })}</li>
          {response?.finishReason ? <li>{t('llmLog.finishReason', { value: response.finishReason })}</li> : null}
          {detail.requestId ? <li>{t('llmLog.requestId', { value: detail.requestId })}</li> : null}
          {response?.usage ? (
            <li>
              {t('llmLog.usage', {
                input: response.usage.inputTokens ?? 0,
                output: response.usage.outputTokens ?? 0,
              })}
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(1)} s`;
}
