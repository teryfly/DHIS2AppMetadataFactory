/*
 * UI-ADM-004: system settings — idempotency & event-stream retention, default approval reminder,
 * upload limits, platform notification channels, archived-project retention.
 *
 * Two shapes meet here and getting them wrong failed silently: `GET /settings` answers a page
 * envelope whose typed half is `values` (S5IF-FLD10), while `PUT /settings` takes the seven keys and
 * 404s on any other key rather than ignoring it. Reading the envelope and writing it back therefore
 * showed every field as empty and made every save fail — while the button reported success only
 * after a re-read that had the same bug.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button, Card } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { NoticeBar } from '@/components/NoticeBar';
import { SelectInput, TextInput, CheckboxInput } from '@/components/form';
import { pushToast } from '@/components/toast';
import { systemSettingsOptions } from '@/api/queries';
import { ACTION } from '@/api/actions';
import { isApiError } from '@/api/errors';
import { useSaveSettings } from '@/api/mutations';
import { LanguageSwitch } from '@/components/LanguageSwitch';
import { translateErrorCode } from '@/i18n';
import { channelLabel } from '@/lib/labels';

/** `NotificationChannels` — the channel value S1 stores as an array, not an object. */
type Channel = { type?: string; target?: string; secretRef?: string };

type Values = {
  idempotencyRetentionHours?: number;
  eventRetentionDays?: number;
  defaultGateReminderHours?: number;
  maxUploadBytes?: number;
  archivedProjectRetentionDays?: number;
  allowedUploadTypes?: string[];
  platformNotificationChannels?: Channel[];
  stageAutoContinue?: string[];
};

/** Design stages that can follow a predecessor automatically.  REQUIREMENTS is excluded: it is
 * seeded READY at project creation, before any material exists. */
const AUTO_CONTINUE_STAGES = ['DOMAIN', 'DISCOVERY', 'MAPPING', 'PAGE_DESIGN', 'BASELINE_COMPILE', 'HANDOFF'] as const;

const NUMBER_KEYS = [
  'idempotencyRetentionHours',
  'eventRetentionDays',
  'defaultGateReminderHours',
  'maxUploadBytes',
  'archivedProjectRetentionDays',
] as const;
type NumberKey = (typeof NUMBER_KEYS)[number];

function normalize(raw: unknown): Values {
  const values = (raw as { values?: Values } | undefined)?.values ?? {};
  return {
    ...values,
    platformNotificationChannels: values.platformNotificationChannels ?? [],
    stageAutoContinue: values.stageAutoContinue ?? [],
  };
}

export function SystemSettingsView() {
  const { t } = useTranslation();
  const settingsQuery = useQuery(systemSettingsOptions());
  const save = useSaveSettings();
  const [form, setForm] = useState<Values>({ platformNotificationChannels: [], stageAutoContinue: [] });
  const [saved, setSaved] = useState<Values>({ platformNotificationChannels: [], stageAutoContinue: [] });

  useEffect(() => {
    if (!settingsQuery.data) return;
    const next = normalize(settingsQuery.data);
    setForm(next);
    setSaved(next);
  }, [settingsQuery.data]);

  if (settingsQuery.isLoading) return <Loading />;

  const num = (v: number | undefined) => (v == null ? '' : String(v));
  // An emptied field must stay absent, not become 0: `Number('')` is 0, and 0 is a real retention
  // period as far as S1 is concerned.
  const setNumber = (key: NumberKey, raw: string) =>
    setForm((prev) => ({ ...prev, [key]: raw.trim() === '' ? undefined : Number(raw) }));
  const channels = () => form.platformNotificationChannels ?? [];
  const writeChannels = (next: Channel[]) =>
    setForm((prev) => ({ ...prev, platformNotificationChannels: next }));
  const setChannel = (index: number, patch: Partial<Channel>) =>
    writeChannels(channels().map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const autoContinue = form.stageAutoContinue ?? [];
  const toggleAutoContinue = (stageType: string, checked: boolean) => {
    const next = checked
      ? [...new Set([...autoContinue, stageType])]
      : autoContinue.filter((value) => value !== stageType);
    setForm((prev) => ({ ...prev, stageAutoContinue: next }));
  };

  const changed = (): Record<string, unknown> => {
    const body: Record<string, unknown> = {};
    for (const key of NUMBER_KEYS) {
      if (form[key] !== saved[key]) body[key] = form[key];
    }
    if (JSON.stringify(channels()) !== JSON.stringify(saved.platformNotificationChannels ?? [])) {
      body.platformNotificationChannels = channels();
    }
    if (JSON.stringify([...autoContinue].sort()) !== JSON.stringify([...(saved.stageAutoContinue ?? [])].sort())) {
      body.stageAutoContinue = [...autoContinue];
    }
    return body;
  };

  const dirty = Object.keys(changed()).length > 0;
  const submit = () => {
    const body = changed();
    if (Object.keys(body).length === 0) return;
    save.mutate(
      { body: body as Record<string, unknown> },
      {
        onSuccess: () => {
          setSaved((prev) => ({ ...prev, ...body }) as Values);
          pushToast({ tone: 'success', title: t('settings.saved') });
        },
      },
    );
  };

  const failure = save.error;

  return (
    <PageContainer
      title={t('settings.title')}
      description={t('page.settings')}
      helpKey="settings"
      actions={<LanguageSwitch />}
    >
      {settingsQuery.isError ? (
        <NoticeBar tone="error" title={t('settings.loadFailed')}>
          {t('settings.loadFailedBody')}
        </NoticeBar>
      ) : null}

      <Card>
        <div className="stack" style={{ maxWidth: 520 }}>
          <TextInput label={t('settings.idempotencyRetention')} type="number" value={num(form.idempotencyRetentionHours)} onChange={(v) => setNumber('idempotencyRetentionHours', v)} />
          <TextInput label={t('settings.eventRetention')} type="number" value={num(form.eventRetentionDays)} onChange={(v) => setNumber('eventRetentionDays', v)} />
          <TextInput label={t('settings.approvalReminder')} type="number" value={num(form.defaultGateReminderHours)} onChange={(v) => setNumber('defaultGateReminderHours', v)} />
          <TextInput label={t('settings.maxUploadBytes')} type="number" value={num(form.maxUploadBytes)} onChange={(v) => setNumber('maxUploadBytes', v)} />
          <TextInput label={t('settings.archivedRetention')} type="number" value={num(form.archivedProjectRetentionDays)} onChange={(v) => setNumber('archivedProjectRetentionDays', v)} />
          <div>
            <label className="muted" style={{ fontSize: 12 }}>{t('settings.uploadLimits')}</label>
            <p className="mono">{(form.allowedUploadTypes ?? []).join(', ')}</p>
          </div>

          <div>
            <label className="muted" style={{ fontSize: 12 }}>{t('settings.autoContinueTitle')}</label>
            <p className="muted" style={{ fontSize: 12, margin: '4px 0 8px' }}>
              {t('settings.autoContinueHint')}
            </p>
            <div className="stack" style={{ gap: 4 }}>
              {AUTO_CONTINUE_STAGES.map((stageType) => (
                <CheckboxInput
                  key={stageType}
                  label={t(`stageTypes.${stageType}`, { defaultValue: stageType })}
                  checked={autoContinue.includes(stageType)}
                  onChange={(checked: boolean) => toggleAutoContinue(stageType, checked)}
                />
              ))}
            </div>
          </div>

          <div>
            <label className="muted" style={{ fontSize: 12 }}>{t('settings.notificationChannels')}</label>
            {channels().length === 0 ? (
              <p className="muted">{t('settings.noChannels')}</p>
            ) : null}
            {channels().map((c, i) => (
              <div className="row" key={i} style={{ alignItems: 'flex-end', gap: 8 }}>
                <SelectInput
                  label={t('settings.channelType')}
                  value={(c.type ?? 'WEBHOOK').toUpperCase()}
                  onChange={(v: string) => setChannel(i, { type: v })}
                  options={[
                    { label: channelLabel('WEBHOOK'), value: 'WEBHOOK' },
                    { label: channelLabel('EMAIL'), value: 'EMAIL' },
                  ]}
                />
                <TextInput label={t('settings.channelTarget')} value={c.target ?? ''} onChange={(v) => setChannel(i, { target: v })} />
                <TextInput label={t('settings.channelSecretRef')} value={c.secretRef ?? ''} onChange={(v) => setChannel(i, { secretRef: v })} helpText={t('environments.secretRefHint')} />
                <Button secondary destructive onClick={() => writeChannels(channels().filter((_, j) => j !== i))}>
                  {t('settings.removeChannel')}
                </Button>
              </div>
            ))}
            <Button
              secondary
              onClick={() => writeChannels([...channels(), { type: 'WEBHOOK', target: '', secretRef: '' }])}
            >
              {t('settings.addChannel')}
            </Button>
          </div>

          {failure && isApiError(failure) ? (
            <NoticeBar tone="error" title={t('settings.saveFailed')}>
              <div className="stack" style={{ gap: 6 }}>
                <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
                {failure.correlationId ? (
                  <span className="mono muted" style={{ fontSize: 12 }}>
                    {t('common.correlationId')}: {failure.correlationId}
                  </span>
                ) : null}
                <div>
                  <Button secondary onClick={submit}>
                    {t('common.retry')}
                  </Button>
                </div>
              </div>
            </NoticeBar>
          ) : null}

          <div className="row">
            {/* UI-BR-01: this used to be enabled on "an operator has been typed in" alone, which is
                the UI granting itself a permission. `settings.manage` answers that, and the reason
                appears on the button when S1 says no. */}
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.settingsManage}
              tone="primary"
              loading={save.isPending}
              onExecute={submit}
            >
              {t('settings.save')}
            </AllowedActionButton>
            {!dirty ? <span className="muted">{t('settings.nothingChanged')}</span> : null}
          </div>
        </div>
      </Card>
    </PageContainer>
  );
}
