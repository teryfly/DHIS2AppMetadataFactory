/*
 * UI-PRJ-004: the interface for S1 project management — target environments (add / remove / reorder,
 * change the design reference with its invalidation warning), delivery-mode upgrade (A→B),
 * notification settings and the approval reminder, archive, delete (archived only, typed
 * confirmation), export and import.
 *
 * Every number on this screen is read back from S1 before it can be edited. An earlier version
 * rendered local defaults, which meant that pressing Save wrote values nobody had chosen.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Modal, ModalTitle, ModalContent, ModalActions } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { NoticeBar } from '@/components/NoticeBar';
import { Heading } from '@/components/typography';
import { SelectInput, TextInput } from '@/components/form';
import { pushToast } from '@/components/toast';
import {
  projectOptions,
  projectSettingsOptions,
  targetEnvironmentsOptions,
  uidPoolOptions,
  environmentsOptions,
} from '@/api/queries';
import { ACTION } from '@/api/actions';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import {
  useArchiveProject,
  useDeleteProject,
  useExportProject,
  useImportProject,
  useReplaceTargetEnvironments,
  useSaveProjectSettings,
  useUpgradeDeliveryMode,
} from '@/api/mutations';
import type * as T from '@/api/types';
import { deliveryModeLabel } from '@/lib/labels';

type Target = NonNullable<T.TargetEnvironmentListView['items']>[number];

export function ProjectSettingsView() {
  const { t } = useTranslation();
  const { projectId = '' } = useParams();
  const navigate = useNavigate();

  const project = useQuery(projectOptions(projectId));
  const settings = useQuery(projectSettingsOptions(projectId));
  const targets = useQuery(targetEnvironmentsOptions(projectId));
  const uidPool = useQuery(uidPoolOptions(projectId));
  const allEnvs = useQuery(environmentsOptions());

  const replaceTargets = useReplaceTargetEnvironments(projectId);
  const saveSettings = useSaveProjectSettings(projectId);
  const upgrade = useUpgradeDeliveryMode(projectId);
  const archiveProject = useArchiveProject(projectId);
  const exportProject = useExportProject(projectId);
  const deleteProject = useDeleteProject(projectId);
  const importProject = useImportProject();

  const [rows, setRows] = useState<Target[]>([]);
  const [reminder, setReminder] = useState('');
  const [emails, setEmails] = useState('');
  const [webhookRef, setWebhookRef] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteWord, setDeleteWord] = useState('');
  const [confirmUpgrade, setConfirmUpgrade] = useState(false);
  const [importArtifact, setImportArtifact] = useState('');

  useEffect(() => {
    if (targets.data) setRows((targets.data.items ?? []) as Target[]);
  }, [targets.data]);

  useEffect(() => {
    const s = settings.data;
    if (!s) return;
    setReminder(s.gateReminderHours == null ? '' : String(s.gateReminderHours));
    setEmails((s.notificationEmails ?? []).join(', '));
    setWebhookRef(String(s.notificationWebhookRef ?? ''));
  }, [settings.data]);

  if (project.isLoading) return <Loading />;
  const p = project.data;
  if (!p) return <NoticeBar tone="error">{t('errors.NOT_FOUND')}</NoticeBar>;
  const workflowId = p.workflowId ?? undefined;

  const move = (index: number, delta: number) => {
    const to = index + delta;
    if (to < 0 || to >= rows.length) return;
    const next = [...rows];
    const [row] = next.splice(index, 1);
    if (row) next.splice(to, 0, row);
    setRows(next);
  };
  const setReference = (key: string) =>
    setRows(rows.map((r) => ({ ...r, isDesignReference: r.environmentKey === key })));
  const removeRow = (key: string) => setRows(rows.filter((r) => r.environmentKey !== key));
  const addRow = (key: string) => {
    if (!key || rows.some((r) => r.environmentKey === key)) return;
    const env = (allEnvs.data?.items ?? [] as unknown as Record<string, unknown>[]).find((e) => String(e.environmentKey ?? e.key ?? '') === key);
    setRows([...rows, { environmentKey: key, environmentClass: String(env?.environmentClass ?? 'TEST'), isDesignReference: rows.length === 0 } as unknown as Target]);
  };

  const targetsDirty =
    JSON.stringify(rows) !== JSON.stringify((targets.data?.items ?? []) as Target[]);

  const saveTargets = () =>
    replaceTargets.mutate(
      { targetEnvironments: rows as unknown as Record<string, unknown>[] },
      { onSuccess: () => pushToast({ tone: 'success', title: t('projectSettings.saved') }) },
    );

  const settingsBody = (): T.ProjectSettingsBody => ({
    gateReminderHours: reminder.trim() === '' ? undefined : Number(reminder),
    notificationEmails: emails
      .split(',')
      .map((e) => e.trim())
      .filter(Boolean),
    notificationWebhookRef: webhookRef.trim() === '' ? undefined : webhookRef.trim(),
  });
  const settingsDirty =
    reminder !== String(settings.data?.gateReminderHours ?? '') ||
    emails !== (settings.data?.notificationEmails ?? []).join(', ') ||
    webhookRef !== String(settings.data?.notificationWebhookRef ?? '');

  return (
    <PageContainer title={t('projectSettings.title')} description={t('page.projectSettings')} helpKey="manage">
      <div className="stack">
        {/* ---------------------------------------------------------------- environments */}
        <div className="panel">
          <Heading level={5} size="small">{t('wizard.targetsLabel')}</Heading>
          <p className="muted">{t('projectSettings.targetsHelp')}</p>
          {rows.length === 0 ? <p className="muted">{t('projectSettings.noTargets')}</p> : null}
          <ol className="stack" style={{ gap: 6 }}>
            {rows.map((r, i) => (
              <li key={String(r.environmentKey)} className="row" style={{ gap: 8, alignItems: 'center' }}>
                <span className="mono">{String(r.environmentKey)}</span>
                <span className="muted">{t(`environments.class${String(r.environmentClass ?? 'TEST')}`, { defaultValue: String(r.environmentClass ?? '') })}</span>
                {r.isDesignReference ? <span title={t('projectSettings.designRef')}>★ {t('projectSettings.designRef')}</span> : null}
                <Button small secondary disabled={i === 0} onClick={() => move(i, -1)}>{t('projectSettings.moveUp')}</Button>
                <Button small secondary disabled={i === rows.length - 1} onClick={() => move(i, 1)}>{t('projectSettings.moveDown')}</Button>
                <Button small secondary disabled={r.isDesignReference} onClick={() => setReference(String(r.environmentKey))}>{t('projectSettings.makeReference')}</Button>
                <Button small secondary destructive onClick={() => removeRow(String(r.environmentKey))}>{t('projectSettings.removeTarget')}</Button>
              </li>
            ))}
          </ol>
          <div className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
            <SelectInput
              label={t('projectSettings.addTarget')}
              value=""
              onChange={(v: string) => addRow(v)}
              options={(allEnvs.data?.items ?? [])
                .map((e) => String((e as unknown as Record<string, unknown>).environmentKey ?? (e as unknown as Record<string, unknown>).key ?? ''))
                .filter((k) => k && !rows.some((r) => String(r.environmentKey) === k))
                .map((k) => ({ label: k, value: k }))}
            />
            <AllowedActionButton workflowId={workflowId} action={ACTION.projectEdit} tone="primary" loading={replaceTargets.isPending} disabled={!targetsDirty} onExecute={saveTargets}>
              {t('projectSettings.saveTargets')}
            </AllowedActionButton>
          </div>
          <NoticeBar tone="warning" title={t('projectSettings.designRefWarning')}>
            {t('projectSettings.designRefWarningBody')}
          </NoticeBar>
        </div>

        {/* ---------------------------------------------------------------- notification settings */}
        <div className="panel">
          <Heading level={5} size="small">{t('projectSettings.notifications')}</Heading>
          <div className="stack" style={{ maxWidth: 420 }}>
            <TextInput
              label={t('projectSettings.reminder')}
              type="number"
              value={reminder}
              onChange={setReminder}
              helpText={t('projectSettings.reminderHelp')}
            />
            <TextInput label={t('projectSettings.emails')} value={emails} onChange={setEmails} placeholder="a@example.org, b@example.org" />
            <TextInput label={t('projectSettings.webhookRef')} value={webhookRef} onChange={setWebhookRef} helpText={t('environments.secretRefHint')} />
            <div className="row">
              <AllowedActionButton workflowId={workflowId} action={ACTION.projectEdit} tone="primary" loading={saveSettings.isPending} disabled={!settingsDirty} onExecute={() => saveSettings.mutate(settingsBody(), { onSuccess: () => pushToast({ tone: 'success', title: t('projectSettings.saved') }) })}>
                {t('common.save')}
              </AllowedActionButton>
            </div>
          </div>
        </div>

        {/* ---------------------------------------------------------------- delivery mode */}
        <div className="panel">
          <Heading level={5} size="small">{t('projectSettings.mode')}</Heading>
          <p className="muted">
            {t('projectSettings.modeNow', { mode: deliveryModeLabel(p.deliveryMode ?? 'A') })} · {t('projectSettings.uidAvailable', { n: String(uidPool.data?.available ?? '—') })}
          </p>
          {uidPool.data?.needsReplenishment ? (
            <NoticeBar tone="warning" title={t('projectSettings.uidLow')}>{t('projectSettings.uidLowBody')}</NoticeBar>
          ) : null}
          {String(p.deliveryMode ?? 'A') === 'A' ? (
            <div className="row">
              <AllowedActionButton workflowId={workflowId} action={ACTION.projectUpgradeMode} tone="secondary" loading={upgrade.isPending} onExecute={() => setConfirmUpgrade(true)}>
                {t('projectSettings.upgrade')}
              </AllowedActionButton>
            </div>
          ) : null}
        </div>

        {/* ---------------------------------------------------------------- export / archive / delete */}
        <div className="panel">
          <Heading level={5} size="small">{t('projectSettings.dangerZone')}</Heading>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.projectExport}
              tone="secondary"
              loading={exportProject.isPending}
              onExecute={() =>
                exportProject.mutate(undefined, {
                  // The export is accepted as a job; the file itself becomes a PROJECT_EXPORT
                  // artifact. Saying "accepted" and sending the operator to where it lands is the
                  // honest version of a button that used to discard the answer.
                  onSuccess: () => pushToast({ tone: 'success', title: t('projectSettings.exportAccepted'), body: t('projectSettings.exportWhere') }),
                })
              }
            >
              {t('projectSettings.export')}
            </AllowedActionButton>
            <AllowedActionButton workflowId={workflowId} action={ACTION.projectArchive} tone="secondary" loading={archiveProject.isPending} onExecute={() => archiveProject.mutate(undefined, { onSuccess: () => pushToast({ tone: 'success', title: t('projectSettings.archived') }) })}>
              {t('projectSettings.archive')}
            </AllowedActionButton>
            <AllowedActionButton workflowId={workflowId} action={ACTION.projectDelete} tone="destructive" onExecute={() => setConfirmDelete(true)}>
              {t('common.delete')}
            </AllowedActionButton>
            <Button secondary onClick={() => navigate(`/projects/${projectId}/overview`)}>
              {t('common.back')}
            </Button>
          </div>
          <p className="muted">{t('projectSettings.archiveBeforeDelete')}</p>
        </div>

        {/* ---------------------------------------------------------------- import */}
        <div className="panel">
          <Heading level={5} size="small">{t('projectSettings.import')}</Heading>
          <p className="muted">{t('projectSettings.importHelp')}</p>
          <div className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
            <TextInput label={t('projectSettings.importArtifact')} value={importArtifact} onChange={setImportArtifact} />
            <AllowedActionButton
              workflowId={workflowId}
              scope="platform"
              action={ACTION.projectImport}
              tone="primary"
              loading={importProject.isPending}
              disabled={!importArtifact.trim()}
              onExecute={() =>
                importProject.mutate({ artifactId: importArtifact.trim() }, {
                  onSuccess: (res) => {
                    pushToast({ tone: 'success', title: t('projectSettings.importAccepted') });
                    const restored = String((res as unknown as { stepRunId?: string }).stepRunId ?? '');
                    if (restored) navigate(`/projects/${restored}/overview`);
                  },
                })
              }
            >
              {t('projectSettings.import')}
            </AllowedActionButton>
          </div>
        </div>
      </div>

      {confirmDelete ? (
      <Modal position="middle" hide={false} onClose={() => setConfirmDelete(false)}>
        <ModalTitle>{t('projectSettings.deleteTitle')}</ModalTitle>
        <ModalContent>
          <div className="stack">
            <p>{t('projectSettings.deleteBody', { name: String(p.name ?? projectId) })}</p>
            <TextInput label={t('projectSettings.deleteConfirmField')} value={deleteWord} onChange={setDeleteWord} />
          </div>
        </ModalContent>
        <ModalActions>
          <Button secondary onClick={() => setConfirmDelete(false)}>{t('common.cancel')}</Button>
          <Button
            destructive
            disabled={deleteWord.trim() !== String(p.name ?? '')}
            loading={deleteProject.isPending}
            onClick={() => {
              deleteProject.mutate(undefined, {
                onSuccess: () => {
                  setConfirmDelete(false);
                  pushToast({ tone: 'success', title: t('projectSettings.deleted') });
                  navigate('/projects');
                },
              });
            }}
          >
            {t('common.delete')}
          </Button>
        </ModalActions>
      </Modal>
      ) : null}

      {confirmUpgrade ? (
      <Modal position="middle" hide={false} onClose={() => setConfirmUpgrade(false)}>
        <ModalTitle>{t('projectSettings.upgradeTitle')}</ModalTitle>
        <ModalContent>
          <p>{t('projectSettings.upgradeBody')}</p>
        </ModalContent>
        <ModalActions>
          <Button secondary onClick={() => setConfirmUpgrade(false)}>{t('common.cancel')}</Button>
          <Button
            primary
            loading={upgrade.isPending}
            onClick={() =>
              upgrade.mutate({}, {
                onSuccess: () => {
                  setConfirmUpgrade(false);
                  pushToast({ tone: 'success', title: t('projectSettings.upgraded') });
                },
              })
            }
          >
            {t('projectSettings.upgrade')}
          </Button>
        </ModalActions>
      </Modal>
      ) : null}
    </PageContainer>
  );
}
