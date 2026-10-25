/*
 * UI-OPS-003: notification centre — pending gates, rejected submissions, execution completion /
 * failure. Filter by project, mark read. The unread badge lives in the top bar.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, Button } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';
import { SelectInput } from '@/components/form';
import { PAGE_SIZE, notificationsOptions, projectsListOptions } from '@/api/queries';
import { useMarkNotificationRead } from '@/api/mutations';
import { formatDate } from '@/lib/time';
import { notificationTypeLabel } from '@/lib/labels';

export function NotificationsView() {
  const { t, i18n } = useTranslation();
  const [projectId, setProjectId] = useState('');
  // Both filters are S1's, not local predicates (CP-NTF-001): the feed is paged, so filtering here
  // would mean "unread among the newest page" and the count beside it would be a different number
  // from the one in the top bar.
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [mine, setMine] = useState(false);
  const [offset, setOffset] = useState(0);
  const projects = useQuery(projectsListOptions());
  const notifications = useQuery(notificationsOptions(projectId || undefined, { unreadOnly, mine, offset }));
  const markRead = useMarkNotificationRead();
  const items = notifications.data?.items ?? [];
  const total = notifications.data?.total ?? items.length;
  const at = projects.data?.items ?? [];

  const toggle = (next: { unreadOnly?: boolean; mine?: boolean }) => {
    setUnreadOnly((current) => next.unreadOnly ?? current);
    setMine((current) => next.mine ?? current);
    setOffset(0);
  };

  return (
    <PageContainer title={t('notifications.title')} description={t('page.notifications')} helpKey="notifications">
      <div className="row" style={{ marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <SelectInput
          label={t('notifications.filterProject')}
          value={projectId}
          onChange={(value) => {
            setProjectId(value);
            setOffset(0);
          }}
          options={[{ value: '', label: t('common.all') }, ...at.map((p) => ({ value: p.id, label: p.name }))]}
        />
        <label className="muted" style={{ fontSize: 12, alignSelf: 'flex-end' }}>
          <input type="checkbox" checked={unreadOnly} onChange={(event) => toggle({ unreadOnly: event.target.checked })} />{' '}
          {t('notifications.filterUnread')}
        </label>
        <label className="muted" style={{ fontSize: 12, alignSelf: 'flex-end' }}>
          <input type="checkbox" checked={mine} onChange={(event) => toggle({ mine: event.target.checked })} />{' '}
          {t('notifications.filterMine')}
        </label>
      </div>

      {notifications.isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState>{t('notifications.empty')}</EmptyState>
      ) : (
        <div className="stack">
          <p className="muted" style={{ fontSize: 12 }}>{t('notifications.shownOf', { shown: items.length, total })}</p>
          {items.map((n) => (
            <Card key={n.id}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <div className="stack" style={{ gap: 2 }}>
                  {/* Which kind of notice this is, in the operator's language, and then S1's own
                      sentence under it. The server writes these; the console neither re-writes nor
                      hides them — but a row whose only heading is English prose is a row a Chinese
                      operator has to read twice, and the kind is the part they are scanning for. */}
                  <strong>{notificationTypeLabel(n.type)}</strong>
                  {n.message ? <span style={{ fontSize: 13 }}>{n.message}</span> : null}
                  <span className="muted" style={{ fontSize: 12 }}>
                    {n.projectId ? <Link to={`/projects/${n.projectId}/overview`}>{n.projectId}</Link> : ''} · {formatDate(n.createdAt, i18n.language)}
                  </span>
                </div>
                {!n.read ? (
                  <Button secondary small onClick={() => markRead.mutate({ notificationId: n.id })}>
                    {t('notifications.markRead')}
                  </Button>
                ) : (
                  <span className="muted">{t('common.ok')}</span>
                )}
              </div>
            </Card>
          ))}
          {/* S5IF-G04: the rest of the feed is one request away, and the alternative — a list that
              stops without saying it stopped — is a wrong count. */}
          {items.length < total ? (
            <Button secondary small onClick={() => setOffset((current) => current + PAGE_SIZE)}>
              {t('common.loadMore')}
            </Button>
          ) : null}
        </div>
      )}
    </PageContainer>
  );
}
