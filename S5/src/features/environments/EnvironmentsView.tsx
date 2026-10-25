/*
 * UI-ADM-001: environments — create/edit (baseUrl, class, version constraint, auth mode, credential
 * Secret references, TLS, data policy, timeout, commit windows), connection test, emergency lock,
 * expiring credentials.
 * UI-ADM-002 / UI-AC-06 / UI-BR-03 as amended (契约 1.7.0): the field holds a Secret *reference*.
 * An operator who has none yet can enter the credential once (S5IF-SEC01) and the reference comes
 * back filled in; the value itself is never displayed again, masked or otherwise.
 *
 * Every option list here mirrors S1's contract (see `@/api/enums`), because an invented value is a
 * 422 carrying `details.allowed` — a worse experience than a select that cannot offer it.
 *
 * Two things the form cannot get wrong, because S4 reads them as the environment's own rules:
 * the class is chosen at registration and never again, and a PRODUCTION environment needs at
 * least one commit window. Both are enforced here so neither arrives as an answer the operator
 * cannot act on.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Button, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Modal, ModalTitle, ModalContent, ModalActions } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { EmptyState } from '@/components/EmptyState';
import { StatCard } from '@/components/SectionCard';
import { StatusBadge } from '@/components/StatusBadge';
import { NoticeBar } from '@/components/NoticeBar';
import { Heading } from '@/components/typography';
import { TextInput, TextAreaInput, SelectInput, CheckboxInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { FormShell } from '@/components/FormShell';
import { CredentialField } from '@/features/secrets/CredentialField';
import { ACTION, useAllowedActions } from '@/api/actions';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import {
  AUTH_MODES,
  CREDENTIAL_SCOPES,
  DATA_POLICIES,
  ENVIRONMENT_CLASSES,
  REQUIRED_CREDENTIAL_SCOPES,
  isSecretReference,
} from '@/api/enums';
import {
  environmentProtectionOptions,
  environmentsOptions,
  expiringCredentialsOptions,
} from '@/api/queries';
import {
  useCreateEnvironment,
  useDisableEnvironment,
  useEmergencyLock,
  useProbeCapabilities,
  useTestConnection,
  useUpdateEnvironment,
} from '@/api/mutations';
import type * as T from '@/api/types';
import { credentialScopeLabel } from '@/lib/labels';

/** The Administration pages ask the platform-scope answer; they own no workflow. */
const PLATFORM = 'platform' as const;

type CredentialDraft = { scope: string; secretRef: string };
type WindowDraft = { days: number[]; start: string; end: string };

/** S4's `CommitWindow`: ISO weekdays, 1 = Monday. */
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7];
const CLOCK = /^([01]?\d|2[0-3]):[0-5]\d$/;

export function EnvironmentsView() {
  const { t } = useTranslation();
  const envs = useQuery(environmentsOptions());
  const expiring = useQuery(expiringCredentialsOptions());
  const lock = useEmergencyLock();
  const probe = useProbeCapabilities();
  const disable = useDisableEnvironment();
  const test = useTestConnection();
  const { check } = useAllowedActions(undefined, PLATFORM);
  const [editing, setEditing] = useState<T.EnvironmentView | null>(null);
  const [creating, setCreating] = useState(false);
  const [lockTarget, setLockTarget] = useState<{ environment: T.EnvironmentView; locked: boolean } | null>(null);
  const [lockReason, setLockReason] = useState('');
  const [disableTarget, setDisableTarget] = useState<T.EnvironmentView | null>(null);
  const [disableReason, setDisableReason] = useState('');
  const [probeResult, setProbeResult] = useState('');
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  // Registration is not the same as usability: the validation stage refuses an environment that has
  // never passed a connection test, so the page has to say it out loud right after the save.
  const [savedNotice, setSavedNotice] = useState(false);

  const items = envs.data?.items ?? [];
  const locking = lockTarget?.locked !== true;

  return (
    <PageContainer
      title={t('environments.title')}
      description={t('page.environments')}
      helpKey="environments"
      actions={
        <AllowedActionButton workflowId={undefined} scope={PLATFORM} action={ACTION.environmentCreate} onExecute={() => setCreating(true)}>
          {t('environments.create')}
        </AllowedActionButton>
      }
    >
      {items.length === 0 && envs.isLoading ? <Loading /> : null}

      <div className="stat-grid" style={{ marginBottom: 16 }}>
        <StatCard label={t('environments.statsTotal')} value={items.length} icon="server" tone="brand" />
        <StatCard
          label={t('environments.statsActive')}
          value={items.filter((environment) => environment.status === 'ACTIVE').length}
          icon="check"
          tone="success"
        />
        <StatCard
          label={t('environments.statsDisabled')}
          value={items.filter((environment) => environment.status !== 'ACTIVE').length}
          icon="lock"
        />
        <StatCard
          label={t('environments.statsLocked')}
          value={items.filter((environment) => check(ACTION.environmentEmergencyLock, environment.environmentKey).details?.locked === true).length}
          icon="alert"
          tone="warning"
        />
      </div>

      {savedNotice ? (
        <NoticeBar tone="info" title={t('environments.saved')}>
          <Button secondary small onClick={() => setSavedNotice(false)}>{t('common.close')}</Button>
        </NoticeBar>
      ) : null}

      {/* An empty table with no sentence reads as a broken page, not as "nothing configured yet" —
          and this is the very screen the getting-started checklist sends people to first. */}
      {items.length === 0 && !envs.isLoading ? (
        <EmptyState action={<Link to="/getting-started">{t('environments.openSetup')}</Link>}>
          {t('environments.empty')}
        </EmptyState>
      ) : null}

      <div className="table-wrap">
      <DataTable>
        <DataTableHead>
          <DataTableRow>
            <DataTableCell tag="th">{t('environments.key')}</DataTableCell>
            <DataTableCell tag="th">{t('environments.class')}</DataTableCell>
            <DataTableCell tag="th">{t('common.status')}</DataTableCell>
            <DataTableCell tag="th">{t('environments.baseUrl')}</DataTableCell>
            <DataTableCell tag="th">{t('common.actions')}</DataTableCell>
          </DataTableRow>
        </DataTableHead>
        <DataTableBody>
          {items.map((e) => {
            // FR-UI-009 / UI-BR-01: the lock is S1's live protection state, and it reaches this
            // row as `details.locked` on the very action the button is gated by. `status` is
            // ACTIVE or DISABLED — an emergency lock is not a status, and reading one from it
            // would show an unlocked row as locked, or the reverse.
            const locked = check(ACTION.environmentEmergencyLock, e.environmentKey).details?.locked === true;
            return (
              <DataTableRow key={e.environmentKey}>
                <DataTableCell>
                  <Link to={`/environments/${e.environmentKey}`}>{e.name ?? e.environmentKey}</Link>
                </DataTableCell>
                <DataTableCell>{t(`environments.class${e.environmentClass}`, { defaultValue: e.environmentClass })}</DataTableCell>
                <DataTableCell>
                  <div className="row" style={{ gap: 4 }}>
                    <StatusBadge status={e.status} />
                    {locked ? <StatusBadge status="LOCKED" /> : null}
                  </div>
                </DataTableCell>
                <DataTableCell className="mono">{e.baseUrl}</DataTableCell>
                <DataTableCell>
                  <div className="table-actions">
                    <AllowedActionButton
                      workflowId={undefined}
                      scope={PLATFORM}
                      action={ACTION.environmentEdit}
                      targetId={e.environmentKey}
                      tone="secondary"
                      onExecute={() => setEditing(e)}
                    >
                      {t('common.edit')}
                    </AllowedActionButton>
                    <AllowedActionButton
                      workflowId={undefined}
                      scope={PLATFORM}
                      action={ACTION.environmentTestConnection}
                      targetId={e.environmentKey}
                      tone="secondary"
                      loading={test.isPending}
                      onExecute={() =>
                        test.mutate(
                          { environmentKey: e.environmentKey, requiredScopes: [...REQUIRED_CREDENTIAL_SCOPES] },
                          { onSuccess: (r) => setTestResult(r as Record<string, unknown>) },
                        )
                      }
                    >
                      {t('environments.testConnection')}
                    </AllowedActionButton>
                    {/* Two more operations S1 offers per environment and neither console had a
                        way to reach: switching an environment out of service, and asking the
                        instance what version it is. Both were API-only before. */}
                    <AllowedActionButton
                      workflowId={undefined}
                      scope={PLATFORM}
                      action={ACTION.environmentProbeCapabilities}
                      targetId={e.environmentKey}
                      tone="secondary"
                      loading={probe.isPending}
                      onExecute={() =>
                        probe.mutate(
                          { environmentKey: e.environmentKey },
                          { onSuccess: (r) => setProbeResult(`${e.environmentKey}: ${String((r as Record<string, unknown>).version ?? '—')}`) },
                        )
                      }
                    >
                      {t('environments.probeCapabilities')}
                    </AllowedActionButton>
                    <AllowedActionButton
                      workflowId={undefined}
                      scope={PLATFORM}
                      action={ACTION.environmentDisable}
                      targetId={e.environmentKey}
                      tone="secondary"
                      onExecute={() => {
                        setDisableReason('');
                        setDisableTarget(e);
                      }}
                    >
                      {t('environments.disable')}
                    </AllowedActionButton>
                    <AllowedActionButton
                      workflowId={undefined}
                      scope={PLATFORM}
                      action={ACTION.environmentEmergencyLock}
                      targetId={e.environmentKey}
                      tone={locked ? 'primary' : 'destructive'}
                      onExecute={() => {
                        setLockReason('');
                        setLockTarget({ environment: e, locked });
                      }}
                    >
                      {t(locked ? 'environments.releaseLock' : 'environments.emergencyLock')}
                    </AllowedActionButton>
                  </div>
                </DataTableCell>
              </DataTableRow>
            );
          })}
        </DataTableBody>
      </DataTable>
      </div>

      {testResult ? <ConnectionResult result={testResult} /> : null}
      {probeResult ? (
        <NoticeBar tone="valid" title={t('environments.probeCapabilities')}>
          <span className="mono">{probeResult}</span>
        </NoticeBar>
      ) : null}

      {disableTarget && (
        <Modal position="bottom" hide={false} onClose={() => setDisableTarget(null)}>
          <ModalTitle>{t('environments.disableTitle')}</ModalTitle>
          <ModalContent>
            <div className="stack">
              <p className="muted">{disableTarget.name ?? disableTarget.environmentKey}</p>
              {/* S4 keeps the row and refuses every operation against it. Nothing switches it back
                  on — no endpoint carries an enable — so the dialog has to say that rather than
                  promise a way back. The profile stays editable, and the lock stays liftable. */}
              <p>{t('environments.disableBody')}</p>
              <TextAreaInput
                label={t('environments.disableReason')}
                value={disableReason}
                rows={2}
                onChange={setDisableReason}
              />
            </div>
          </ModalContent>
          <ModalActions>
            <Button secondary onClick={() => setDisableTarget(null)}>{t('common.cancel')}</Button>
            <AllowedActionButton
              workflowId={undefined}
              scope={PLATFORM}
              action={ACTION.environmentDisable}
              targetId={disableTarget.environmentKey}
              tone="destructive"
              loading={disable.isPending}
              onExecute={async () => {
                await disable.mutateAsync({
                  environmentKey: disableTarget.environmentKey,
                  reason: disableReason.trim() || undefined,
                });
                setDisableTarget(null);
                void envs.refetch();
              }}
            >
              {t('common.confirm')}
            </AllowedActionButton>
          </ModalActions>
        </Modal>
      )}

      <Heading level={4} size="small" className="section-title">{t('environments.expiringCredentials')}</Heading>
      {renderExpiring(expiring.data, t)}

      {(editing || creating) && (
        <EnvironmentForm
          environment={editing}
          onClose={() => { setEditing(null); setCreating(false); }}
          onSaved={() => { setEditing(null); setCreating(false); setSavedNotice(true); void envs.refetch(); }}
        />
      )}

      {lockTarget && (
        <Modal position="bottom" hide={false} onClose={() => setLockTarget(null)}>
          <ModalTitle>{t(locking ? 'environments.lockTitle' : 'environments.releaseLockTitle')}</ModalTitle>
          <ModalContent>
            <div className="stack">
              <p className="muted">{lockTarget.environment.name ?? lockTarget.environment.environmentKey}</p>
              {/* S1 refuses a lock without a reason: the audit chain has to say who decided and why.
                  Releasing does not need one, and asking would turn the way out into a second form. */}
              <TextAreaInput
                label={t('environments.lockReason')}
                value={lockReason}
                rows={2}
                onChange={setLockReason}
                error={lockReason || !locking ? undefined : t('environments.lockReasonMissing')}
              />
            </div>
          </ModalContent>
          <ModalActions>
            <Button secondary onClick={() => setLockTarget(null)}>{t('common.cancel')}</Button>
            <AllowedActionButton
              workflowId={undefined}
              scope={PLATFORM}
              action={ACTION.environmentEmergencyLock}
              targetId={lockTarget.environment.environmentKey}
              tone={locking ? 'destructive' : 'primary'}
              loading={lock.isPending}
              disabled={locking && !lockReason.trim()}
              onExecute={async () => {
                await lock.mutateAsync({
                  environmentKey: lockTarget.environment.environmentKey,
                  locked: locking,
                  reason: lockReason.trim() || undefined,
                });
                setLockTarget(null);
                void envs.refetch();
              }}
            >
              {t('common.confirm')}
            </AllowedActionButton>
          </ModalActions>
        </Modal>
      )}
    </PageContainer>
  );
}

/**
 * S1 answers a connection test with the eight DX-CON-001 checks (`state`, not `passed`) and
 * `missingAuthorities` as a map of authority set to the scopes that lacked it.
 */
function ConnectionResult({ result }: { result: Record<string, unknown> }) {
  const { t } = useTranslation();
  const checks = (result.checks ?? []) as { name?: string; state?: string; detail?: string }[];
  const missing = Object.entries((result.missingAuthorities ?? {}) as Record<string, unknown>);
  const ok = Boolean(result.ok);
  return (
    <NoticeBar tone={ok ? 'valid' : 'warning'} title={t('environments.connectionResult')}>
      <div className="stack" style={{ fontSize: 13 }}>
        {/* S1 stamps the answer with when it probed, and says in one line what it found. Without
            the timestamp an operator cannot tell a fresh test from a replayed one. */}
        {result.message ? <span>{String(result.message)}</span> : null}
        <span>
          DHIS2: {String(result.dhis2Version ?? '—')}
          {result.testedAt ? ` · ${t('environments.testedAt')}: ${String(result.testedAt)}` : ''}
        </span>
        {checks.length ? (
          <ul>
            {checks.map((c, i) => (
              <li key={i}>
                {c.name} — {t(c.state === 'PASSED' ? 'environments.checkPassed' : c.state === 'SKIPPED' ? 'environments.checkSkipped' : 'environments.checkFailed')}
                {c.detail ? <span className="muted"> · {String(c.detail)}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
        {missing.length ? (
          <span>
            {t('environments.missingPermissions')}:{' '}
            {missing.map(([scope, authorities]) => `${credentialScopeLabel(String(scope))}: ${String(authorities)}`).join('; ')}
          </span>
        ) : null}
      </div>
    </NoticeBar>
  );
}

function renderExpiring(data: T.ExpiringCredentialList | undefined, t: (k: string) => string) {
  const items = data?.items ?? [];
  if (items.length === 0) return <p className="muted">{t('environments.noExpiring')}</p>;
  return (
    <ul>
      {items.map((c, i) => (
        <li key={i} className="mono">
          {c.environmentKey} · {credentialScopeLabel(String(c.scope ?? ''))} · {c.secretRef} → {String(c.expiresAt ?? '—')}
          {c.daysRemaining != null ? ` (${c.daysRemaining})` : ''}
        </li>
      ))}
    </ul>
  );
}

/**
 * One form for both create and edit. The difference is not cosmetic: S1's update body carries
 * every field as optional and reads "absent" as "unchanged", so the edit path sends its own
 * patch rather than the whole record.
 */
export function EnvironmentForm({
  environment,
  onClose,
  onSaved,
  embedded,
}: {
  environment: T.EnvironmentView | null;
  onClose: () => void;
  onSaved?: () => void;
  /** Rendered inside the getting-started step rather than as a modal (UI-SET-006). */
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const [environmentKey, setEnvironmentKey] = useState(environment?.environmentKey ?? '');
  const [name, setName] = useState(environment?.name ?? '');
  const [baseUrl, setBaseUrl] = useState(environment?.baseUrl ?? '');
  const [environmentClass, setEnvironmentClass] = useState<string>(environment?.environmentClass ?? 'TEST');
  const [authMode, setAuthMode] = useState<string>(environment?.authMode ?? 'PAT');
  const [versionConstraint, setVersionConstraint] = useState(environment?.versionConstraint ?? '');
  const [dataPolicy, setDataPolicy] = useState<string>(environment?.llmDataPolicy ?? 'PRIVATE_NETWORK');
  const [timeout, setTimeout_] = useState(String(environment?.timeoutSeconds ?? 60));
  const [tls, setTls] = useState(environment?.tlsRequired ?? true);
  const [notes, setNotes] = useState(environment?.notes ?? '');
  const [credentials, setCredentials] = useState<CredentialDraft[]>(() =>
    environment?.credentials?.length
      ? environment.credentials.map((c) => ({ scope: String(c.scope ?? 'discovery'), secretRef: String(c.secretRef ?? '') }))
      : [{ scope: 'discovery', secretRef: '' }, { scope: 'import', secretRef: '' }],
  );

  /**
   * `null` means "the operator has not touched this", and the value on screen is then whatever S1
   * reported. A patch that replaced the stored windows with an empty list because the form opened
   * before they loaded would be the worst kind of save: it succeeds.
   */
  const [timezone, setTimezone] = useState<string | null>(null);
  const [windows, setWindows] = useState<WindowDraft[] | null>(null);

  const create = useCreateEnvironment();
  const update = useUpdateEnvironment(environment?.environmentKey ?? '');
  const protection = useQuery(environmentProtectionOptions(environment?.environmentKey ?? ''));
  const failure = create.error ?? update.error;
  const pending = create.isPending || update.isPending;

  // S4's rules read the class the environment was registered with; on an edit that is a fact
  // about the record, not a choice this form still has.
  const environmentClassEffective = environment?.environmentClass ?? environmentClass;
  const production = environmentClassEffective === 'PRODUCTION';
  const shownTimezone = timezone ?? protection.data?.timezone ?? 'UTC';
  /**
   * On an edit the windows come from S1, not from the form: offering the editor before they
   * arrive would let a save replace the stored schedule with an empty list.
   */
  const protectionLoaded = !environment || protection.isSuccess;
  const shownWindows: WindowDraft[] =
    windows ??
    (protection.data?.commitWindows ?? []).map((w) => ({
      days: w.days ?? [],
      start: w.start ?? '00:00',
      end: w.end ?? '23:59',
    }));
  const protectionTouched = timezone !== null || windows !== null;

  const refs = credentials.filter((c) => isSecretReference(c.secretRef));
  const badRefs = credentials.some((c) => c.secretRef.trim() !== '' && !isSecretReference(c.secretRef));
  const duplicateScopes = CREDENTIAL_SCOPES.filter((scope) => refs.filter((c) => c.scope === scope).length > 1);
  // S4 requires a discovery and an import reference from EVERY environment class
  // (environment/rules.py:85-96); PRODUCTION additionally forbids sharing one (DX-AC-02) and
  // needs at least one commit window (DX-ENV-002).
  const missingScopes = REQUIRED_CREDENTIAL_SCOPES.filter((scope) => !refs.some((c) => c.scope === scope));
  const sharedProductionRef = production && missingScopes.length === 0 && !distinctRefs(refs);
  const badWindow = shownWindows.some((w) => w.days.length === 0 || !CLOCK.test(w.start) || !CLOCK.test(w.end));
  /**
   * DX-ENV-002 bites a creation always and an amendment only when the operator reached for it:
   * a production environment registered before this form existed must still be editable on its
   * other fields, and blocking that would be the form inventing a problem S1 does not have.
   */
  const windowsProblem = production && (environment ? protectionTouched : true) && (shownWindows.length === 0 || badWindow);
  // The scheme is checked whether or not TLS is *required*: `http://` under a TLS-optional
  // environment is still an address this deployment cannot use, and S4 refuses anything else.
  const wrongScheme = !/^https?:\/\//.test(baseUrl.trim());
  const insecure = tls && !baseUrl.trim().startsWith('https://');
  const keyHasSpace = /\s/.test(environmentKey);
  const timeoutNumber = Number(timeout);

  const credentialsPayload = () => refs.map((c) => ({ scope: c.scope, secretRef: c.secretRef.trim() }));

  const protectionPayload = (): T.ProtectionSpec => ({
    timezone: shownTimezone,
    // S4 reads the lock through `set_emergency_lock` alone (DX-ENV-007); saying `false` here is
    // the form declaring that it is not that operation, which is also what the contract asks for.
    emergencyLock: false,
    commitWindows: shownWindows.map((w) => ({
      days: [...w.days].sort((a, b) => a - b),
      start: w.start,
      end: w.end,
    })),
  });

  const patchWindow = (index: number, change: Partial<WindowDraft>): void =>
    setWindows(shownWindows.map((w, i) => (i === index ? { ...w, ...change } : w)));

  const createBody = (): T.CreateEnvironmentRequest => ({
    environmentKey: environmentKey.trim(),
    name: name.trim() || undefined,
    baseUrl: baseUrl.trim(),
    environmentClass: environmentClass as T.CreateEnvironmentRequest['environmentClass'],
    versionConstraint: versionConstraint.trim(),
    authMode: authMode as T.CreateEnvironmentRequest['authMode'],
    tlsRequired: tls,
    llmDataPolicy: dataPolicy as T.CreateEnvironmentRequest['llmDataPolicy'],
    timeoutSeconds: timeoutNumber,
    notes: notes.trim() || undefined,
    credentials: credentialsPayload() as T.CredentialSpec[],
    // Only a production environment is asked for windows: handing a DEV environment a commit
    // schedule would register a rule its deployments never made.
    ...(production ? { protection: protectionPayload() } : {}),
  });

  /**
   * Only what the operator actually changed. S1's update body treats an absent field as
   * "unchanged", so sending the whole record back would rewrite columns nobody touched — and
   * `notes` is a field S4 lets an operator empty, which only an explicit "" achieves. Omitting
   * an emptied one would silently restore the stored value after a reload.
   *
   * `name` is not one of them: S4 re-validates the merged profile and refuses an empty display
   * name, so an emptied name is held back as a local error instead of sent as `""`. Neither is
   * the class — DX-ENV-001 chooses it at registration, and S1 refuses it by name on a patch.
   */
  const patchBody = (): Partial<T.UpdateEnvironmentRequest> => {
    if (!environment) return {};
    const out: Record<string, unknown> = {};
    const put = (field: string, next: unknown, before: unknown): void => {
      if (next !== before) out[field] = next;
    };
    if (name.trim() !== (environment.name ?? '')) out.name = name.trim();
    put('notes', notes.trim(), environment.notes ?? '');
    put('baseUrl', baseUrl.trim(), environment.baseUrl ?? '');
    put('authMode', authMode, environment.authMode);
    put('llmDataPolicy', dataPolicy, environment.llmDataPolicy);
    put('versionConstraint', versionConstraint.trim(), environment.versionConstraint ?? '');
    put('tlsRequired', tls, environment.tlsRequired);
    put('timeoutSeconds', timeoutNumber, environment.timeoutSeconds);
    const storedRefs = (environment.credentials ?? []).map((c) => `${c.scope}\u0000${c.secretRef}`).join('\n');
    const nowRefs = refs.map((c) => `${c.scope}\u0000${c.secretRef.trim()}`).join('\n');
    if (nowRefs !== storedRefs) out.credentials = credentialsPayload();
    if (protectionTouched) out.protection = protectionPayload();
    return out as Partial<T.UpdateEnvironmentRequest>;
  };

  const patch = patchBody();
  const valid =
    environmentKey.trim() !== '' &&
    !keyHasSpace &&
    baseUrl.trim() !== '' &&
    !wrongScheme &&
    versionConstraint.trim() !== '' &&
    !badRefs &&
    duplicateScopes.length === 0 &&
    missingScopes.length === 0 &&
    !sharedProductionRef &&
    !insecure &&
    Number.isFinite(timeoutNumber) &&
    timeoutNumber > 0 &&
    !windowsProblem &&
    (!environment || name.trim() !== '') &&
    (!environment || Object.keys(patch).length > 0);

  return (
    <FormShell
      embedded={embedded}
      title={environment ? t('common.edit') : t('environments.create')}
      onClose={onClose}
      footer={<>
        <Button secondary onClick={onClose}>{t('common.cancel')}</Button>
        <AllowedActionButton
          workflowId={undefined}
          scope={PLATFORM}
          action={environment ? ACTION.environmentEdit : ACTION.environmentCreate}
          targetId={environment?.environmentKey}
          loading={pending}
          disabled={!valid}
          onExecute={async () => {
            if (environment) {
              await update.mutateAsync(patch);
            } else {
              await create.mutateAsync(createBody());
            }
            if (onSaved) onSaved();
            else onClose();
          }}
        >
          {t('environments.save')}
        </AllowedActionButton>
      </>}
    >
          <NoticeBar tone="info" title={t('environments.secretsOnlyTitle')}>
            {t('environments.secretsOnlyBody')}
          </NoticeBar>
          {failure && isApiError(failure) ? (
            <NoticeBar tone="error" title={t('error.title')}>
              <div className="stack" style={{ gap: 4 }}>
                <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
                {failure.correlationId ? (
                  <span className="mono muted" style={{ fontSize: 12 }}>
                    {t('common.correlationId')}: {failure.correlationId}
                  </span>
                ) : null}
              </div>
            </NoticeBar>
          ) : null}

          <TextInput
            label={t('environments.key')}
            value={environmentKey}
            onChange={setEnvironmentKey}
            required
            disabled={!!environment}
            error={keyHasSpace ? t('environments.keyNoSpaces') : undefined}
          />
          <TextInput
            label={t('environments.name')}
            value={name}
            onChange={setName}
            required={!!environment}
            helpText={environment ? undefined : t('environments.nameFallsBackToKey')}
            error={environment && name.trim() === '' ? t('environments.nameRequired') : undefined}
          />
          <TextInput
            label={t('environments.baseUrl')}
            value={baseUrl}
            onChange={setBaseUrl}
            required
            type="url"
            error={
              wrongScheme
                ? t('environments.httpSchemeRequired')
                : insecure
                  ? t('environments.httpsRequired')
                  : undefined
            }
          />
          <SelectInput
            label={t('environments.class')}
            value={environmentClassEffective}
            onChange={setEnvironmentClass}
            disabled={!!environment}
            helpText={environment ? t('environments.classImmutable') : undefined}
            options={optionsWithCurrent(ENVIRONMENT_CLASSES, environmentClassEffective, (value) =>
              t(`environments.class${value}`, { defaultValue: value }),
            )}
          />
          <SelectInput
            label={t('environments.authMode')}
            value={authMode}
            onChange={setAuthMode}
            options={optionsWithCurrent(AUTH_MODES, authMode, (value) =>
              t(`environments.auth${value}`, { defaultValue: value }),
            )}
          />
          <TextInput
            label={t('environments.versionConstraint')}
            value={versionConstraint}
            onChange={setVersionConstraint}
            placeholder="2.41.x"
            required
            helpText={t('environments.versionConstraintHint')}
            error={versionConstraint.trim() === '' ? t('environments.versionConstraintRequired') : undefined}
          />
          <SelectInput
            label={t('environments.dataPolicy')}
            value={dataPolicy}
            onChange={setDataPolicy}
            options={optionsWithCurrent(DATA_POLICIES, dataPolicy, (value) =>
              t(`environments.policy${value}`, { defaultValue: value }),
            )}
          />
          <TextInput label={t('environments.timeoutSeconds')} value={timeout} onChange={setTimeout_} type="number" required />

          <Heading level={5} size="small">{t('environments.credentials')}</Heading>
          {missingScopes.length ? (
            <p className="field__error">{t('environments.credentialScopesRequired')}: {missingScopes.join(', ')}</p>
          ) : null}
          {duplicateScopes.length ? (
            <p className="field__error">{t('environments.credentialScopeDuplicate')}: {duplicateScopes.join(', ')}</p>
          ) : null}
          {production ? (
            <p className="field__error">{t('environments.productionRefs')}</p>
          ) : null}
          {credentials.map((row, index) => (
            <div className="row" key={index} style={{ alignItems: 'flex-end', gap: 8 }}>
              <div style={{ flex: '0 0 200px' }}>
                <SelectInput
                  label={t('environments.credentialScope')}
                  value={row.scope}
                  onChange={(scope) => setCredentials(credentials.map((c, i) => (i === index ? { ...c, scope } : c)))}
                  options={optionsWithCurrent(CREDENTIAL_SCOPES, row.scope, (value) =>
                    credentialScopeLabel(String(value)),
                  )}
                />
              </div>
              <div style={{ flex: '1 1 auto' }}>
                <CredentialField
                  label={t('environments.secretRef')}
                  value={row.secretRef}
                  onChange={(secretRef) => setCredentials(credentials.map((c, i) => (i === index ? { ...c, secretRef } : c)))}
                  // BASIC needs one stored value shaped `user:password` (S4 client/auth.py), so the
                  // entry control asks for both halves and composes them before posting.
                  shape={authMode === 'BASIC' ? 'basic' : 'single'}
                  suggestedName={`dhis2-${(environmentKey.trim() || 'env').toLowerCase().replace(/[^a-z0-9._-]+/g, '-')}-${row.scope}`}
                  error={row.secretRef.trim() !== '' && !isSecretReference(row.secretRef) ? t('environments.referenceRequired') : undefined}
                />
              </div>
              <Button secondary onClick={() => setCredentials(credentials.filter((_, i) => i !== index))}>
                {t('environments.removeCredential')}
              </Button>
            </div>
          ))}
          {/* S4 keys one credential per scope, so a second row for a scope the form already has
              is either dropped by the server or overwrites the first — never offer it. */}
          <Button
            secondary
            onClick={() => setCredentials([...credentials, { scope: nextScope(credentials), secretRef: '' }])}
          >
            {t('environments.addCredential')}
          </Button>

          <CheckboxInput label={t('environments.tlsRequired')} checked={tls} onChange={setTls} />
          <TextAreaInput label={t('environments.notes')} value={notes} rows={2} onChange={setNotes} />

          {production ? (
            <>
              <Heading level={5} size="small">{t('environments.commitWindows')}</Heading>
              <p className="muted">{t('environments.commitWindowsHint')}</p>
              {environment && !protectionLoaded ? (
                <p className="muted">{t('environments.commitWindowsLoading')}</p>
              ) : null}
              <TextInput
                label={t('environments.timezone')}
                value={shownTimezone}
                onChange={setTimezone}
                helpText={t('environments.timezoneHint')}
              />
              {shownWindows.map((window, index) => (
                <div className="stack" key={index} style={{ gap: 4 }}>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                    {WEEKDAYS.map((day) => (
                      <CheckboxInput
                        key={day}
                        label={t(`environments.weekday${day}`)}
                        checked={window.days.includes(day)}
                        onChange={(on) =>
                          setWindows(
                            shownWindows.map((w, i) =>
                              i === index
                                ? { ...w, days: on ? [...w.days, day].sort((a, b) => a - b) : w.days.filter((d) => d !== day) }
                                : w,
                            ),
                          )
                        }
                      />
                    ))}
                  </div>
                  <div className="row" style={{ alignItems: 'flex-end', gap: 8 }}>
                    <div style={{ flex: '0 0 140px' }}>
                      <TextInput
                        label={t('environments.windowStart')}
                        value={window.start}
                        onChange={(start) => patchWindow(index, { start })}
                        placeholder="01:00"
                        error={!CLOCK.test(window.start) ? t('environments.clockFormat') : undefined}
                      />
                    </div>
                    <div style={{ flex: '0 0 140px' }}>
                      <TextInput
                        label={t('environments.windowEnd')}
                        value={window.end}
                        onChange={(end) => patchWindow(index, { end })}
                        placeholder="05:00"
                        error={!CLOCK.test(window.end) ? t('environments.clockFormat') : undefined}
                      />
                    </div>
                    <Button secondary onClick={() => setWindows(shownWindows.filter((_, i) => i !== index))}>
                      {t('environments.removeWindow')}
                    </Button>
                  </div>
                </div>
              ))}
              <Button
                secondary
                onClick={() => setWindows([...shownWindows, { days: [], start: '00:00', end: '23:59' }])}
              >
                {t('environments.addWindow')}
              </Button>
              {windowsProblem ? <p className="field__error">{t('environments.windowsRequired')}</p> : null}
            </>
          ) : null}
    </FormShell>
  );
}

/**
 * The contract's values, plus the stored one when it is not among them. A row written before the
 * enum was enforced would otherwise render as an empty select, and an empty-looking field invites
 * the operator to "fix" a value that is not actually wrong for that environment.
 */
function optionsWithCurrent<T extends string>(values: readonly T[], current: string, label: (v: string) => string) {
  const known = values as readonly string[];
  const list = current && !known.includes(current) ? [current, ...known] : [...known];
  return list.map((value) => ({ value, label: label(value) }));
}

/** The first scope the form is not already using; when all are, the last one repeats none. */
function nextScope(credentials: CredentialDraft[]): string {
  return CREDENTIAL_SCOPES.find((scope) => !credentials.some((c) => c.scope === scope)) ?? CREDENTIAL_SCOPES[0]!;
}

/** DX-AC-02: a production environment must not share one secret between discovery and import. */
function distinctRefs(refs: CredentialDraft[]): boolean {
  const discovery = refs.find((c) => c.scope === 'discovery')?.secretRef;
  const importRef = refs.find((c) => c.scope === 'import')?.secretRef;
  if (!discovery || !importRef) return false;
  return discovery.trim() !== importRef.trim();
}
